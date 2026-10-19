"""
Product Pricing Comparison across Supported Delivery Pincodes.
Evaluates displayed price, delivery charges, eligible discounts,
payment-method-specific UPI discounts, and final payable price.
"""

import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, Any, List, Optional, Union

from cart import extract_product_id, get_product_info, add_product_to_cart
from config import PRICE_TYPE_BASIC
from meesho_client import MeeshoClient
from optimizer import check_shipping_charges_fast, update_cart_location


logger = logging.getLogger("PricingComparator")

# Comprehensive representative pincode benchmark covering all 8 Indian postal zones
ALL_INDIA_PINCODES = [
    # Zone 1: North (Delhi, Haryana, Punjab, HP, J&K)
    {"pin": "110001", "city": "New Delhi", "state": "Delhi", "zone": "Zone 1 (North)"},
    {"pin": "122001", "city": "Gurgaon", "state": "Haryana", "zone": "Zone 1 (North)"},
    {"pin": "141001", "city": "Ludhiana", "state": "Punjab", "zone": "Zone 1 (North)"},
    {"pin": "171001", "city": "Shimla", "state": "Himachal Pradesh", "zone": "Zone 1 (North)"},
    {"pin": "180001", "city": "Jammu", "state": "Jammu & Kashmir", "zone": "Zone 1 (North)"},
    {"pin": "190001", "city": "Srinagar", "state": "Jammu & Kashmir", "zone": "Zone 1 (North)"},

    # Zone 2: North-Central (UP, Uttarakhand)
    {"pin": "201301", "city": "Noida", "state": "Uttar Pradesh", "zone": "Zone 2 (North-Central)"},
    {"pin": "208001", "city": "Kanpur", "state": "Uttar Pradesh", "zone": "Zone 2 (North-Central)"},
    {"pin": "226001", "city": "Lucknow", "state": "Uttar Pradesh", "zone": "Zone 2 (North-Central)"},
    {"pin": "248001", "city": "Dehradun", "state": "Uttarakhand", "zone": "Zone 2 (North-Central)"},
    {"pin": "282001", "city": "Agra", "state": "Uttar Pradesh", "zone": "Zone 2 (North-Central)"},

    # Zone 3: West (Gujarat, Rajasthan)
    {"pin": "302001", "city": "Jaipur", "state": "Rajasthan", "zone": "Zone 3 (West)"},
    {"pin": "342001", "city": "Jodhpur", "state": "Rajasthan", "zone": "Zone 3 (West)"},
    {"pin": "380001", "city": "Ahmedabad", "state": "Gujarat", "zone": "Zone 3 (West)"},
    {"pin": "395001", "city": "Surat", "state": "Gujarat", "zone": "Zone 3 (West)"},
    {"pin": "390001", "city": "Vadodara", "state": "Gujarat", "zone": "Zone 3 (West)"},

    # Zone 4: West-Central (Maharashtra, MP, Chhattisgarh, Goa)
    {"pin": "400001", "city": "Mumbai", "state": "Maharashtra", "zone": "Zone 4 (West-Central)"},
    {"pin": "411001", "city": "Pune", "state": "Maharashtra", "zone": "Zone 4 (West-Central)"},
    {"pin": "440001", "city": "Nagpur", "state": "Maharashtra", "zone": "Zone 4 (West-Central)"},
    {"pin": "403001", "city": "Panaji", "state": "Goa", "zone": "Zone 4 (West-Central)"},
    {"pin": "452001", "city": "Indore", "state": "Madhya Pradesh", "zone": "Zone 4 (West-Central)"},
    {"pin": "462001", "city": "Bhopal", "state": "Madhya Pradesh", "zone": "Zone 4 (West-Central)"},
    {"pin": "492001", "city": "Raipur", "state": "Chhattisgarh", "zone": "Zone 4 (West-Central)"},

    # Zone 5: South (AP, Telangana, Karnataka)
    {"pin": "500001", "city": "Hyderabad", "state": "Telangana", "zone": "Zone 5 (South)"},
    {"pin": "520001", "city": "Vijayawada", "state": "Andhra Pradesh", "zone": "Zone 5 (South)"},
    {"pin": "530001", "city": "Visakhapatnam", "state": "Andhra Pradesh", "zone": "Zone 5 (South)"},
    {"pin": "560001", "city": "Bangalore", "state": "Karnataka", "zone": "Zone 5 (South)"},
    {"pin": "570001", "city": "Mysore", "state": "Karnataka", "zone": "Zone 5 (South)"},

    # Zone 6: South-Peninsular (Tamil Nadu, Kerala)
    {"pin": "600001", "city": "Chennai", "state": "Tamil Nadu", "zone": "Zone 6 (South-Peninsular)"},
    {"pin": "641001", "city": "Coimbatore", "state": "Tamil Nadu", "zone": "Zone 6 (South-Peninsular)"},
    {"pin": "682001", "city": "Kochi", "state": "Kerala", "zone": "Zone 6 (South-Peninsular)"},
    {"pin": "695001", "city": "Thiruvananthapuram", "state": "Kerala", "zone": "Zone 6 (South-Peninsular)"},

    # Zone 7: East & North-East (WB, Odisha, Assam, NE)
    {"pin": "700001", "city": "Kolkata", "state": "West Bengal", "zone": "Zone 7 (East & NE)"},
    {"pin": "734001", "city": "Siliguri", "state": "West Bengal", "zone": "Zone 7 (East & NE)"},
    {"pin": "751001", "city": "Bhubaneswar", "state": "Odisha", "zone": "Zone 7 (East & NE)"},
    {"pin": "781001", "city": "Guwahati", "state": "Assam", "zone": "Zone 7 (East & NE)"},
    {"pin": "793001", "city": "Shillong", "state": "Meghalaya", "zone": "Zone 7 (East & NE)"},

    # Zone 8: East-Central (Bihar, Jharkhand)
    {"pin": "800001", "city": "Patna", "state": "Bihar", "zone": "Zone 8 (East-Central)"},
    {"pin": "834001", "city": "Ranchi", "state": "Jharkhand", "zone": "Zone 8 (East-Central)"},
    {"pin": "831001", "city": "Jamshedpur", "state": "Jharkhand", "zone": "Zone 8 (East-Central)"}
]

ALL_INDIA_PIN_MAP = {entry["pin"]: entry for entry in ALL_INDIA_PINCODES}


def get_all_india_pincodes() -> List[str]:
    """Returns the list of 6-digit postal codes spanning all 8 Indian zones."""
    return [entry["pin"] for entry in ALL_INDIA_PINCODES]


class ComparisonStatus(str, Enum):
    VERIFIED_AVAILABLE = "VERIFIED_AVAILABLE"
    UNSUPPORTED_PINCODE = "UNSUPPORTED_PINCODE"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    PRODUCT_UNAVAILABLE = "PRODUCT_UNAVAILABLE"
    PRICE_MISSING = "PRICE_MISSING"
    FAILED = "FAILED"


@dataclass
class PriceBreakdown:
    """Detailed price components for a single product at a specific pincode."""
    product_id: int
    pincode: str
    status: ComparisonStatus
    city: str = ""
    state: str = ""
    zone: str = ""
    displayed_product_price: float = 0.0
    delivery_charges: float = 0.0
    eligible_discounts: float = 0.0
    payment_method_discounts: float = 0.0
    final_payable_price: float = 0.0
    upi_verified_eligible: bool = False
    delivery_days: Optional[str] = None
    error_message: Optional[str] = None
    raw_details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class PricingComparisonReport:
    """Aggregated comparison report across multiple pincodes."""
    product_id: int
    product_name: Optional[str]
    tested_pincodes: List[str]
    results: List[PriceBreakdown] = field(default_factory=list)
    cheapest_verified_option: Optional[PriceBreakdown] = None
    max_savings_vs_worst: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "tested_pincodes": self.tested_pincodes,
            "cheapest_verified_option": self.cheapest_verified_option.to_dict() if self.cheapest_verified_option else None,
            "max_savings_vs_worst": self.max_savings_vs_worst,
            "results": [r.to_dict() for r in self.results]
        }

    def summary_table(self) -> str:
        lines = [
            f"Pricing Comparison for Product {self.product_id}: {self.product_name or ''}",
            "=" * 92,
            f"{'Pincode':<10} | {'Status':<20} | {'Price':<8} | {'Delivery':<10} | {'Discount':<10} | {'Payable':<10} | {'City':<15}",
            "-" * 92
        ]
        for r in self.results:
            lines.append(
                f"{r.pincode:<10} | "
                f"{r.status.value:<20} | "
                f"₹{r.displayed_product_price:<7.2f} | "
                f"₹{r.delivery_charges:<9.2f} | "
                f"₹{(r.eligible_discounts + r.payment_method_discounts):<9.2f} | "
                f"₹{r.final_payable_price:<9.2f} | "
                f"{r.city[:15]:<15}"
            )
        lines.append("=" * 92)
        if self.cheapest_verified_option:
            opt = self.cheapest_verified_option
            lines.append(
                f"Cheapest Option: Pincode {opt.pincode} ({opt.city}) at Final ₹{opt.final_payable_price:.2f} "
                f"(Saved up to ₹{self.max_savings_vs_worst:.2f})"
            )
        else:
            lines.append("No verified available pricing options found.")
        return "\n".join(lines)


class PricingCache:
    """In-memory cache for pricing lookups with TTL expiration."""

    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}

    def _key(self, product_id: int, pincode: str) -> str:
        return f"{product_id}:{str(pincode).strip()}"

    def get(self, product_id: int, pincode: str) -> Optional[PriceBreakdown]:
        k = self._key(product_id, pincode)
        entry = self._cache.get(k)
        if not entry:
            return None
        if time.time() - entry["timestamp"] > self.ttl:
            del self._cache[k]
            return None
        return entry["data"]

    def set(self, product_id: int, pincode: str, breakdown: PriceBreakdown):
        k = self._key(product_id, pincode)
        self._cache[k] = {
            "timestamp": time.time(),
            "data": breakdown
        }

    def clear(self):
        self._cache.clear()


class ProductPricingComparator:
    """
    Compares final payable prices for a product across candidate pincodes,
    accurately computing price components, verified discounts, and ranking.
    """

    def __init__(self, client: MeeshoClient, cache: Optional[PricingCache] = None):
        self.client = client
        self.cache = cache or PricingCache()

    def compare_product_pricing(
        self,
        product_url_or_id: Union[str, int],
        pincodes: Optional[List[Union[str, int]]] = None,
        price_type: str = PRICE_TYPE_BASIC,
        use_cache: bool = True,
        all_india: bool = False
    ) -> PricingComparisonReport:
        """
        Accepts a product URL or ID and candidate pincodes (or all_india=True), evaluates pricing for each,
        and returns a structured PricingComparisonReport ranked by final payable price.
        """
        if all_india or not pincodes:
            candidate_pins = get_all_india_pincodes()
        else:
            candidate_pins = [str(p).strip() for p in pincodes if str(p).strip()]

        if not candidate_pins:
            raise ValueError("Candidate pincodes list cannot be empty.")

        product_id = extract_product_id(product_url_or_id)

        # 1. Fetch product metadata
        try:
            prod_info = get_product_info(self.client, product_id)
            product_name = prod_info.get("name") or f"Product {product_id}"
            supplier_id = prod_info.get("supplier_id")
            base_price = float(prod_info.get("price") or 0.0)
            variations = prod_info.get("variations") or []
            in_stock_vars = [v for v in variations if v.get("in_stock")]
            chosen_var = in_stock_vars[0] if in_stock_vars else (variations[0] if variations else None)
        except Exception as e:
            logger.error(f"Failed to fetch product metadata for {product_id}: {e}")
            failed_results = [
                PriceBreakdown(
                    product_id=product_id,
                    pincode=p,
                    status=ComparisonStatus.PRODUCT_UNAVAILABLE,
                    error_message=f"Product metadata lookup failed: {e}"
                )
                for p in candidate_pins
            ]
            return PricingComparisonReport(
                product_id=product_id,
                product_name=None,
                tested_pincodes=candidate_pins,
                results=failed_results,
                cheapest_verified_option=None,
                max_savings_vs_worst=0.0
            )

        # Check stock
        if not in_stock_vars:
            out_of_stock_results = [
                PriceBreakdown(
                    product_id=product_id,
                    pincode=p,
                    status=ComparisonStatus.OUT_OF_STOCK,
                    displayed_product_price=base_price,
                    error_message="No variations in stock for selected supplier."
                )
                for p in candidate_pins
            ]
            return PricingComparisonReport(
                product_id=product_id,
                product_name=product_name,
                tested_pincodes=candidate_pins,
                results=out_of_stock_results,
                cheapest_verified_option=None,
                max_savings_vs_worst=0.0
            )

        # 2. Evaluate pricing for each pincode
        results: List[PriceBreakdown] = []

        for pin in candidate_pins:
            if use_cache:
                cached = self.cache.get(product_id, pin)
                if cached:
                    results.append(cached)
                    continue

            breakdown = self._evaluate_single_pincode(
                product_id=product_id,
                pincode=pin,
                supplier_id=supplier_id,
                variation_id=chosen_var["variation_id"],
                variation_name=chosen_var["variation_name"],
                base_price=base_price,
                price_type=price_type
            )
            if use_cache:
                self.cache.set(product_id, pin, breakdown)
            results.append(breakdown)

        # 3. Rank results by final payable price (verified available first)
        verified = [r for r in results if r.status == ComparisonStatus.VERIFIED_AVAILABLE]
        non_verified = [r for r in results if r.status != ComparisonStatus.VERIFIED_AVAILABLE]

        verified.sort(key=lambda r: (r.final_payable_price, r.delivery_charges))
        ranked_results = verified + non_verified

        cheapest = verified[0] if verified else None
        worst = verified[-1] if verified else None
        savings = (worst.final_payable_price - cheapest.final_payable_price) if (worst and cheapest) else 0.0

        return PricingComparisonReport(
            product_id=product_id,
            product_name=product_name,
            tested_pincodes=candidate_pins,
            results=ranked_results,
            cheapest_verified_option=cheapest,
            max_savings_vs_worst=max(0.0, savings)
        )

    def _evaluate_single_pincode(
        self,
        product_id: int,
        pincode: str,
        supplier_id: int,
        variation_id: int,
        variation_name: str,
        base_price: float,
        price_type: str
    ) -> PriceBreakdown:
        """Evaluates pricing breakdown for one pincode via shipping and location quote."""
        # Fast shipping check first

        try:
            ship_info = check_shipping_charges_fast(
                self.client,
                dest_pin=pincode,
                product_id=product_id,
                supplier_id=supplier_id,
                quantity=1
            )
        except Exception as e:
            return PriceBreakdown(
                product_id=product_id,
                pincode=pincode,
                status=ComparisonStatus.FAILED,
                error_message=f"Shipping query failed: {e}"
            )

        if not ship_info.get("serviceable"):
            return PriceBreakdown(
                product_id=product_id,
                pincode=pincode,
                status=ComparisonStatus.UNSUPPORTED_PINCODE,
                error_message=f"Pincode {pincode} is not serviceable."
            )

        fast_charges = float(ship_info.get("charges") or 0.0)
        expected_date = ship_info.get("estimated_date") or ship_info.get("expected_delivery")

        # Now get full cart / bill details for accurate discount & UPI pricing
        try:
            # Add or ensure product is in cart
            add_res = add_product_to_cart(
                self.client,
                product_id=product_id,
                supplier_id=supplier_id,
                variation_id=variation_id,
                variation_name=variation_name,
                quantity=1,
                price_type=price_type
            )
            loc_res = update_cart_location(self.client, dest_pin=pincode)

            raw = loc_res.get("raw_result") or {}
            addr = raw.get("address") or {}
            city = addr.get("city") or loc_res.get("city") or ""
            state = addr.get("state") or loc_res.get("state") or ""

            if not city and str(pincode) in ALL_INDIA_PIN_MAP:
                city = ALL_INDIA_PIN_MAP[str(pincode)]["city"]
            if not state and str(pincode) in ALL_INDIA_PIN_MAP:
                state = ALL_INDIA_PIN_MAP[str(pincode)]["state"]

            # Parse components from price_break_up if present
            price_break_up = raw.get("price_break_up") or []
            prod_price = 0.0
            eligible_discounts = 0.0
            upi_discount = 0.0
            delivery_charges = float(loc_res.get("delivery_charges", fast_charges))

            for item in price_break_up:
                item_type = item.get("type", "")
                item_val = abs(float(item.get("value") or 0.0))

                if item_type == "PRODUCT_PRICE":
                    prod_price = item_val
                elif item_type == "DISCOUNT":
                    # Check sub-details
                    for d in item.get("details", []):
                        d_type = d.get("type", "")
                        d_val = abs(float(d.get("value") or 0.0))
                        if d_type in ("PREPAID_DISCOUNT", "UPI_DISCOUNT"):
                            upi_discount += d_val
                        else:
                            eligible_discounts += d_val
                elif item_type == "ADDITIONAL_CHARGES":
                    for d in item.get("details", []):
                        if d.get("type") == "DELIVERY_CHARGES":
                            delivery_charges = abs(float(d.get("value") or 0.0))

            if prod_price == 0.0:
                prod_price = base_price

            # Check UPI price field
            cart_total = float(loc_res.get("total", prod_price + delivery_charges))
            raw_upi_price = loc_res.get("upi_price")

            upi_verified = False
            if raw_upi_price is not None and float(raw_upi_price) > 0 and float(raw_upi_price) < cart_total:
                upi_verified = True
                calc_upi_disc = cart_total - float(raw_upi_price)
                if upi_discount == 0.0:
                    upi_discount = calc_upi_disc

            # Final payable calculation
            final_payable = prod_price + delivery_charges - eligible_discounts - upi_discount

            return PriceBreakdown(
                product_id=product_id,
                pincode=pincode,
                status=ComparisonStatus.VERIFIED_AVAILABLE,
                city=city,
                state=state,
                displayed_product_price=round(prod_price, 2),
                delivery_charges=round(delivery_charges, 2),
                eligible_discounts=round(eligible_discounts, 2),
                payment_method_discounts=round(upi_discount, 2),
                final_payable_price=round(final_payable, 2),
                upi_verified_eligible=upi_verified,
                delivery_days=expected_date,
                raw_details=loc_res
            )

        except Exception as e:
            # Fallback to fast shipping charges + base product price if full cart location failed
            logger.warning(f"Full cart evaluation failed on pin {pincode}: {e}. Falling back to shipping quote.")
            fallback_city = ""
            fallback_state = ""
            fallback_zone = ""
            if str(pincode) in ALL_INDIA_PIN_MAP:
                fallback_city = ALL_INDIA_PIN_MAP[str(pincode)].get("city", "")
                fallback_state = ALL_INDIA_PIN_MAP[str(pincode)].get("state", "")
                fallback_zone = ALL_INDIA_PIN_MAP[str(pincode)].get("zone", "")

            final_payable = base_price + fast_charges
            return PriceBreakdown(
                product_id=product_id,
                pincode=pincode,
                status=ComparisonStatus.VERIFIED_AVAILABLE,
                city=fallback_city,
                state=fallback_state,
                zone=fallback_zone,
                displayed_product_price=round(base_price, 2),
                delivery_charges=round(fast_charges, 2),
                eligible_discounts=0.0,
                payment_method_discounts=0.0,
                final_payable_price=round(final_payable, 2),
                upi_verified_eligible=False,
                delivery_days=expected_date,
                error_message=f"Cart quote fallback: {e}"
            )
