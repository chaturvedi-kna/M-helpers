"""
Unit tests for account_manager.py: schema validation, token parsing, atomic persistence.
"""

import json
import os
import tempfile
import time
from pathlib import Path
import unittest

from account_manager import (
    validate_account_schema,
    parse_xo_token,
    is_token_expired,
    atomic_save_account_json,
    AccountValidationError,
    MockTokenRefreshProvider
)


class TestAccountManager(unittest.TestCase):

    def setUp(self):
        self.provider = MockTokenRefreshProvider(ttl_seconds=3600)
        self.sample_account = self.provider.refresh({
            "mobile": "9876543210",
            "user_id": 123456789,
            "phone": "+919876543210",
            "instance_id": "test_instance_123"
        })

    def test_schema_validation_valid(self):
        is_valid, errors = validate_account_schema(self.sample_account)
        self.assertTrue(is_valid, f"Expected valid schema, got errors: {errors}")
        self.assertEqual(len(errors), 0)

    def test_schema_validation_missing_user_id(self):
        invalid_data = dict(self.sample_account)
        del invalid_data["user_id"]
        is_valid, errors = validate_account_schema(invalid_data)
        self.assertFalse(is_valid)
        self.assertTrue(any("user_id" in e for e in errors))

    def test_schema_validation_invalid_mobile(self):
        invalid_data = dict(self.sample_account)
        invalid_data["mobile"] = "1234"
        invalid_data["phone"] = "1234"
        is_valid, errors = validate_account_schema(invalid_data)
        self.assertFalse(is_valid)
        self.assertTrue(any("mobile" in e or "phone" in e for e in errors))

    def test_schema_validation_missing_xo(self):
        invalid_data = dict(self.sample_account)
        del invalid_data["xo"]
        is_valid, errors = validate_account_schema(invalid_data)
        self.assertFalse(is_valid)
        self.assertTrue(any("xo" in e for e in errors))

    def test_parse_xo_token_and_expiry(self):
        meta = parse_xo_token(self.sample_account["xo"])
        self.assertEqual(meta.user_id, "123456789")
        self.assertEqual(meta.instance_id, "test_instance_123")
        self.assertFalse(meta.is_expired)
        self.assertGreater(meta.remaining_seconds, 3000)

        # Check expired token
        expired_provider = MockTokenRefreshProvider(ttl_seconds=-10)
        expired_acc = expired_provider.refresh(self.sample_account)
        exp_meta = parse_xo_token(expired_acc["xo"])
        self.assertTrue(exp_meta.is_expired)
        self.assertTrue(is_token_expired(expired_acc))

    def test_atomic_save_and_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test_account.json"

            # 1. First save
            atomic_save_account_json(self.sample_account, file_path, create_backup=True)
            self.assertTrue(file_path.is_file())

            # Read back
            with open(file_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(loaded["user_id"], 123456789)

            # 2. Second save with update creates .bak
            updated_acc = dict(self.sample_account)
            updated_acc["user_id"] = 999999999
            atomic_save_account_json(updated_acc, file_path, create_backup=True)

            bak_file = file_path.with_suffix(".json.bak")
            self.assertTrue(bak_file.is_file())
            with open(bak_file, "r", encoding="utf-8") as f:
                bak_data = json.load(f)
            self.assertEqual(bak_data["user_id"], 123456789)

            # 3. Invalid account must raise AccountValidationError and not overwrite
            invalid_acc = {"user_id": None}
            with self.assertRaises(AccountValidationError):
                atomic_save_account_json(invalid_acc, file_path)

    def test_direct_api_token_refresh_provider_missing_ox(self):
        from account_manager import DirectApiTokenRefreshProvider, TokenRefreshError
        provider = DirectApiTokenRefreshProvider()
        invalid_acc = dict(self.sample_account)
        if "ox" in invalid_acc:
            del invalid_acc["ox"]
        with self.assertRaises(TokenRefreshError) as ctx:
            provider.refresh(invalid_acc)
        self.assertIn("missing 'ox'", str(ctx.exception))

    def test_direct_api_token_refresh_provider_missing_user_id(self):
        from account_manager import DirectApiTokenRefreshProvider, TokenRefreshError
        provider = DirectApiTokenRefreshProvider()
        invalid_acc = {"ox": "some_ox_token"}
        with self.assertRaises(TokenRefreshError) as ctx:
            provider.refresh(invalid_acc)
        self.assertIn("missing 'user_id'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

