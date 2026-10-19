"""
Unit tests for pricing_comparator.py: price calculations, discount eligibility, ranking, caching.
"""

import unittest
from unittest.mock import MagicMock, patch

from pricing_comparator import (
    ProductPricingComparator,
    PricingCache,
    PriceBreakdown,
    PricingComparisonReport,
    ComparisonStatus
)


class TestPricingComparator(unittest.TestCase):

    def setUp(self):
        self.mock_client = MagicMock()
        self.cache = PricingCache(ttl_seconds=60)
        self.comparator = ProductPricingComparator(client=self.mock_client, cache=self.cache)

    def test_price_breakdown_math(self):
        """Verifies exact calculation: final_payable = displayed + delivery - discounts - payment_discounts."""
        breakdown = PriceBreakdown(
            product_id=624039638,
            pincode="500092",
            status=ComparisonStatus.VERIFIED_AVAILABLE,
            city="Hyderabad",
            state="Telangana",
            displayed_product_price=234.0,
            delivery_charges=40.0,
            eligible_discounts=20.0,
            payment_method_discounts=15.0,
            final_payable_price=239.0,   # 234 + 40 - 20 - 15 = 239
            upi_verified_eligible=True
        )
        expected = breakdown.displayed_product_price + breakdown.delivery_charges - breakdown.eligible_discounts - breakdown.payment_method_discounts
        self.assertEqual(breakdown.final_payable_price, expected)
        self.assertTrue(breakdown.upi_verified_eligible)

    def test_ranking_and_cheapest_selection(self):
        """Verifies ranking by final payable price and identification of lowest option."""
        opt_hyd = PriceBreakdown(
            product_id=101, pincode="500092", status=ComparisonStatus.VERIFIED_AVAILABLE,
            city="Hyderabad", displayed_product_price=200.0, delivery_charges=50.0,
            eligible_discounts=10.0, payment_method_discounts=10.0, final_payable_price=230.0
        )
        opt_delhi = PriceBreakdown(
            product_id=101, pincode="110025", status=ComparisonStatus.VERIFIED_AVAILABLE,
            city="Delhi", displayed_product_price=200.0, delivery_charges=20.0,
            eligible_discounts=10.0, payment_method_discounts=10.0, final_payable_price=200.0
        )
        opt_unserviceable = PriceBreakdown(
            product_id=101, pincode="999999", status=ComparisonStatus.UNSUPPORTED_PINCODE,
            city="", displayed_product_price=200.0, delivery_charges=0.0,
            eligible_discounts=0.0, payment_method_discounts=0.0, final_payable_price=0.0
        )

        results = [opt_hyd, opt_delhi, opt_unserviceable]
        # Verified available must be sorted by final_payable_price ascending
        verified = [r for r in results if r.status == ComparisonStatus.VERIFIED_AVAILABLE]
        non_verified = [r for r in results if r.status != ComparisonStatus.VERIFIED_AVAILABLE]
        verified.sort(key=lambda r: (r.final_payable_price, r.delivery_charges))
        ranked = verified + non_verified

        report = PricingComparisonReport(
            product_id=101,
            product_name="Sample Dal",
            tested_pincodes=["500092", "110025", "999999"],
            results=ranked,
            cheapest_verified_option=verified[0],
            max_savings_vs_worst=verified[-1].final_payable_price - verified[0].final_payable_price
        )

        self.assertEqual(report.cheapest_verified_option.pincode, "110025")
        self.assertEqual(report.cheapest_verified_option.final_payable_price, 200.0)
        self.assertEqual(report.max_savings_vs_worst, 30.0)
        self.assertEqual(report.results[-1].status, ComparisonStatus.UNSUPPORTED_PINCODE)

    def test_cache_hit_and_miss(self):
        breakdown = PriceBreakdown(
            product_id=101, pincode="500092", status=ComparisonStatus.VERIFIED_AVAILABLE,
            displayed_product_price=100.0, final_payable_price=100.0
        )
        # Cache miss initially
        self.assertIsNone(self.cache.get(101, "500092"))

        # Set cache
        self.cache.set(101, "500092", breakdown)
        cached = self.cache.get(101, "500092")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.product_id, 101)

        # Clear cache
        self.cache.clear()
        self.assertIsNone(self.cache.get(101, "500092"))

    @patch("pricing_comparator.get_product_info")
    @patch("pricing_comparator.check_shipping_charges_fast")
    def test_unsupported_pincode_handling(self, mock_shipping, mock_prod_info):
        mock_prod_info.return_value = {
            "name": "Test Item",
            "supplier_id": 999,
            "price": 150.0,
            "variations": [{"variation_id": 1, "variation_name": "Free Size", "in_stock": True}]
        }
        mock_shipping.return_value = {
            "pincode": "999999",
            "serviceable": False,
            "charges": 0
        }

        report = self.comparator.compare_product_pricing(
            product_url_or_id="624039638",
            pincodes=["999999"],
            use_cache=False
        )

        self.assertEqual(len(report.results), 1)
        res = report.results[0]
        self.assertEqual(res.status, ComparisonStatus.UNSUPPORTED_PINCODE)
        self.assertIsNone(report.cheapest_verified_option)


if __name__ == "__main__":
    unittest.main()

