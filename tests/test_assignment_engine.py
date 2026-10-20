"""
Unit tests for assignment_engine.py: deterministic seeding, input validation, planning & execution.
"""

import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock

from assignment_engine import (
    validate_product_reference,
    validate_test_address,
    AssignmentRuleConfig,
    AssignmentPlanner,
    AssignmentExecutor,
    AssignmentPlan
)


class TestAssignmentEngine(unittest.TestCase):

    def setUp(self):
        self.sample_products = [
            "624039638",
            "https://www.meesho.com/tatasampann-unpolished-toor-dal-1kg/p/624039638",
            "513926576",
            "545926880"
        ]
        self.sample_addresses = [
            {
                "name": "Aarav Patel",
                "mobile": "9876543210",
                "address_line_1": "Plot No. 12, Cyber Gateway, HITEC City",
                "city": "Hyderabad",
                "state": "Telangana",
                "pin": "500081"
            },
            {
                "name": "Rohit Verma",
                "mobile": "9876543210",
                "address_line_1": "House 42, Pocket B",
                "city": "New Delhi",
                "state": "Delhi",
                "pin": "110025"
            }
        ]
        self.sample_accounts = ["acc_001.json", "acc_002.json"]

    def test_product_validation(self):
        # Valid numerical string
        ok, pid, err = validate_product_reference("624039638")
        self.assertTrue(ok)
        self.assertEqual(pid, 624039638)

        # Valid URL
        ok, pid, err = validate_product_reference("https://www.meesho.com/item/p/12345678")
        self.assertTrue(ok)
        self.assertEqual(pid, 12345678)

        # Invalid
        ok, pid, err = validate_product_reference("invalid_without_digits")
        self.assertFalse(ok)
        self.assertIsNotNone(err)

    def test_address_validation(self):
        # Valid address
        ok, errs = validate_test_address(self.sample_addresses[0])
        self.assertTrue(ok)
        self.assertEqual(len(errs), 0)

        # Missing name
        invalid_addr = dict(self.sample_addresses[0])
        del invalid_addr["name"]
        ok, errs = validate_test_address(invalid_addr)
        self.assertFalse(ok)
        self.assertTrue(any("name" in e for e in errs))

        # Invalid 5-digit pin
        bad_pin_addr = dict(self.sample_addresses[0])
        bad_pin_addr["pin"] = "50009"
        ok, errs = validate_test_address(bad_pin_addr)
        self.assertFalse(ok)
        self.assertTrue(any("pincode" in e for e in errs))

    def test_deterministic_seeding(self):
        """Verifies that identical seed produces identical plan, while different seed differs."""
        config_seed_42 = AssignmentRuleConfig(products_per_account=2, random_seed=42)
        planner_1 = AssignmentPlanner(config_seed_42)
        plan_1 = planner_1.create_plan(self.sample_products, self.sample_addresses, self.sample_accounts)

        planner_2 = AssignmentPlanner(config_seed_42)
        plan_2 = planner_2.create_plan(self.sample_products, self.sample_addresses, self.sample_accounts)

        # Must be identical
        self.assertEqual(plan_1.to_dict(), plan_2.to_dict())

        # Different seed
        config_seed_99 = AssignmentRuleConfig(products_per_account=2, random_seed=99)
        planner_3 = AssignmentPlanner(config_seed_99)
        plan_3 = planner_3.create_plan(self.sample_products, self.sample_addresses, self.sample_accounts)

        # Different assignment mapping
        self.assertNotEqual(
            [a.assigned_products for a in plan_1.assignments],
            [a.assigned_products for a in plan_3.assignments]
        )

    def test_products_per_account_rule(self):
        config = AssignmentRuleConfig(products_per_account=3, random_seed=123)
        planner = AssignmentPlanner(config)
        plan = planner.create_plan(self.sample_products, self.sample_addresses, self.sample_accounts)

        for assign in plan.assignments:
            self.assertEqual(len(assign.assigned_products), 3)

    def test_dry_run_execution(self):
        planner = AssignmentPlanner(AssignmentRuleConfig(random_seed=42))
        plan = planner.create_plan(self.sample_products, self.sample_addresses, self.sample_accounts)

        executor = AssignmentExecutor(plan)
        report = executor.execute_plan(dry_run=True)

        self.assertEqual(report["mode"], "DRY_RUN")
        self.assertEqual(report["total_accounts"], 2)
        for it in report["results"]:
            self.assertEqual(it["status"], "SIMULATED_SUCCESS")
            self.assertEqual(it["assigned_products_count"], 2)

    def test_save_and_load_plan(self):
        planner = AssignmentPlanner(AssignmentRuleConfig(random_seed=42))
        plan = planner.create_plan(self.sample_products, self.sample_addresses, self.sample_accounts)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "plan.json"
            plan.save_to_file(target)
            self.assertTrue(target.is_file())

            with open(target, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(loaded["seed"], 42)
            self.assertEqual(loaded["accounts_count"], 2)

    @patch("assignment_engine.add_product_to_cart")
    @patch("assignment_engine.get_product_info")
    def test_live_cart_failure_reporting(self, mock_info, mock_add_cart):
        """Ensures that cart addition failures during live execution are NOT swallowed as success."""
        mock_info.return_value = {
            "supplier_id": 101,
            "variations": [{"variation_id": 1, "variation_name": "Free Size", "in_stock": True}]
        }
        mock_add_cart.return_value = {"success": False, "error": "Item out of stock"}

        cfg = AssignmentRuleConfig(mode="cart_only", products_per_account=1, random_seed=42)
        planner = AssignmentPlanner(cfg)
        plan = planner.create_plan(self.sample_products[:1], [], ["acc_test"])

        mock_client = MagicMock()
        executor = AssignmentExecutor(plan)
        report = executor.execute_plan(account_client_map={"acc_test": mock_client}, dry_run=False)

        self.assertEqual(report["mode"], "LIVE_EXECUTION")
        res = report["results"][0]
        self.assertEqual(res["status"], "FAILED")
        self.assertIn("Item out of stock", res["error"])
        self.assertFalse(res["added_products"][0]["cart_success"])

    @patch("assignment_engine.add_product_to_cart")
    @patch("assignment_engine.get_product_info")
    def test_live_cart_success_reporting(self, mock_info, mock_add_cart):
        """Ensures that successful cart additions report status SUCCESS."""
        mock_info.return_value = {
            "supplier_id": 101,
            "variations": [{"variation_id": 1, "variation_name": "Free Size", "in_stock": True}]
        }
        mock_add_cart.return_value = {"success": True, "cart_session": "sess_123", "result": {"total_quantity": 1}}

        cfg = AssignmentRuleConfig(mode="cart_only", products_per_account=1, random_seed=42)
        planner = AssignmentPlanner(cfg)
        plan = planner.create_plan(self.sample_products[:1], [], ["acc_test"])

        mock_client = MagicMock()
        executor = AssignmentExecutor(plan)
        report = executor.execute_plan(account_client_map={"acc_test": mock_client}, dry_run=False)

        res = report["results"][0]
        self.assertEqual(res["status"], "SUCCESS")
        self.assertIsNone(res.get("error"))
        self.assertTrue(res["added_products"][0]["cart_success"])


if __name__ == "__main__":
    unittest.main()

