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
    {"pin": "110025", "city": "New Delhi", "state": "Delhi", "zone": "Zone 1 (North)"},
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
    {"pin": "500081", "city": "Hyderabad", "state": "Telangana", "zone": "Zone 5 (South)"},
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
    account_discount: float = 0.0
    payment_method_discounts: float = 0.0
    final_payable_price: float = 0.0
    prepaid_payable_price: Optional[float] = None
    upi_verified_eligible: bool = False
    delivery_days: Optional[str] = None
    account_identifier: Optional[str] = None
    account_verified: bool = False
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
    account_context: Optional[Dict[str, Any]] = None
    results: List[PriceBreakdown] = field(default_factory=list)
    cheapest_verified_option: Optional[PriceBreakdown] = None
    max_savings_vs_worst: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "tested_pincodes": self.tested_pincodes,
            "account_context": self.account_context,
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


def fetch_live_meesho_product_quote(
    client: MeeshoClient,
    product_id: int,
    supplier_id: int,
    base_price: float,
    variation_id: Optional[int] = None,
    variation_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetches the live dynamic discount and prepaid/UPI discount directly from Meesho's backend
    using a single one-off preview quote against the target product and authenticated account session.
    Completely isolated from persistent cart session.
    """
    quote_info = {
        "account_discount": 0.0,
        "prepaid_discount": 0.0,
        "discount_source": "none",
        "raw_breakup": []
    }

    acc_data = getattr(client, "account_data", {}) or {}
    has_auth = bool(acc_data.get("xo") or acc_data.get("ox"))

    # 1. Primary: Authenticated preview quote via POST /api/1.0/cart/add with identifier "buy_now"
    if has_auth and client:
        original_cart_session = getattr(client, "cart_session", None)
        try:
            user_id = acc_data.get("user_id")
            preview_payload = {
                "context": "pdp",
                "identifier": "buy_now",
                "cart_session": None,
                "replaceable": False,
                "items": [{
                    "identifier": "buy_now",
                    "product_id": int(product_id),
                    "supplier_id": int(supplier_id) if supplier_id else 0,
                    "variation_id": variation_id,
                    "variation": variation_name or "Free Size",
                    "quantity": 1,
                    "selected_price_type_id": "basic_return_price",
                    "client_metadata": None
                }],
                "address_id": None,
                "user_id": int(user_id) if (user_id and str(user_id).isdigit()) else None
            }
            res = client.api_call("POST", "/api/1.0/cart/add", payload=preview_payload)
            # Restore persistent cart session immediately
            if hasattr(client, "cart_session"):
                client.cart_session = original_cart_session

            if res and res.get("result"):
                result = res.get("result", {})
                price_break_up = result.get("price_break_up") or []
                quote_info["raw_breakup"] = price_break_up

                # Extract live discounts calculated dynamically by Meesho
                live_discount = 0.0
                for item in price_break_up:
                    if item.get("type") == "DISCOUNT":
                        live_discount += abs(float(item.get("value") or 0.0))

                eff_total = float(result.get("effective_total") or 0.0)
                deferred = float(result.get("effective_total_for_deferred_payment") or result.get("effective_total_for_upi_plugin") or 0.0)

                if live_discount > 0:
                    quote_info["account_discount"] = round(live_discount, 2)
                    quote_info["discount_source"] = "meesho_live_quote"

                if eff_total > 0 and deferred > 0 and eff_total > deferred:
                    quote_info["prepaid_discount"] = round(eff_total - deferred, 2)
                    if quote_info["discount_source"] == "none":
                        quote_info["discount_source"] = "meesho_live_quote"
        except Exception as e:
            if hasattr(client, "cart_session"):
                client.cart_session = original_cart_session
            logger.debug(f"Live cart/add preview quote bypassed: {e}")

    # 2. Secondary check: /api/3.0/product/dynamic for supplier's live prepaid discount
    if quote_info["prepaid_discount"] <= 0 and client:
        try:
            dyn_path = f"/api/3.0/product/dynamic?id={product_id}&context=search&origin=catalog_search_results"
            dyn_res = client.api_call("GET", dyn_path)
            if dyn_res and dyn_res.get("product"):
                suppliers = dyn_res.get("product", {}).get("suppliers") or []
                target_sup = next((s for s in suppliers if s.get("supplier_id") == supplier_id), suppliers[0] if suppliers else None)
                if target_sup:
                    p_disc = float(target_sup.get("prepaid_total_discount_in_rs") or target_sup.get("total_discount_in_rs") or 0.0)
                    if p_disc > 0:
                        quote_info["prepaid_discount"] = round(p_disc, 2)
                        if quote_info["discount_source"] == "none":
                            quote_info["discount_source"] = "meesho_dynamic_catalog"
        except Exception as de:
            logger.debug(f"Live product/dynamic lookup bypassed: {de}")

    return quote_info


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
        use_cache: bool = False,
        all_india: bool = False
    ) -> PricingComparisonReport:
        """
        Accepts a product URL or ID and candidate pincodes (or all_india=True), evaluates pricing for each,
        and returns a structured PricingComparisonReport ranked by lowest final payable price.
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
            supplier_id = prod_info.get("supplier_id") or 0
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
        if not in_stock_vars and variations:
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

        # 2. Extract account context & live promotional discounts
        acc_data = getattr(self.client, "account_data", {}) or {}
        user_id = str(acc_data.get("user_id") or "")
        mobile = str(acc_data.get("mobile") or acc_data.get("phone") or "")
        acc_ident = mobile or user_id or "Anonymous"

        fod_discount = 0.0
        fod_tier = None
        offer_bucket = None
        try:
            from fod_classifier import fetch_live_fod
            fod_record = fetch_live_fod(client=self.client, account_data=acc_data)
            if fod_record:
                fod_tier = str(fod_record.advertised_tier or "")
                offer_bucket = getattr(fod_record, "offer_bucket", None)
                if fod_record.advertised_amount:
                    fod_discount = float(fod_record.advertised_amount)
        except Exception as fe:
            logger.debug(f"Live FOD query note in pricing: {fe}")

        # 2b. Fetch dynamic product discount & prepaid quote directly from Meesho
        chosen_var_id = chosen_var.get("variation_id") or chosen_var.get("id") if chosen_var else None
        chosen_var_name = chosen_var.get("variation_name") or chosen_var.get("name") if chosen_var else None

        live_quote = fetch_live_meesho_product_quote(
            client=self.client,
            product_id=product_id,
            supplier_id=supplier_id,
            base_price=base_price,
            variation_id=chosen_var_id,
            variation_name=chosen_var_name
        )

        live_account_discount = live_quote.get("account_discount") or 0.0
        live_prepaid_discount = live_quote.get("prepaid_discount") or 0.0
        discount_source = live_quote.get("discount_source", "none")

        if live_account_discount > 0:
            applied_account_discount = live_account_discount
        elif fod_discount > 0:
            max_fod_cap = round(base_price * 0.585, 2)
            applied_account_discount = min(base_price, fod_discount, max_fod_cap)
            discount_source = "advertised_tier_fallback"
        else:
            applied_account_discount = 0.0

        if live_prepaid_discount > 0:
            applied_prepaid_discount = live_prepaid_discount
        elif applied_account_discount > 0:
            applied_prepaid_discount = round(min(30.0, max(0.0, base_price * 0.13)), 2)
        else:
            applied_prepaid_discount = 0.0

        account_context = {
            "account_id": user_id or None,
            "user_id": acc_data.get("user_id"),
            "mobile": mobile or None,
            "account_identifier": acc_ident,
            "is_authenticated": bool(acc_data.get("xo")),
            "fod_tier": fod_tier,
            "fod_discount": fod_discount,
            "meesho_live_discount": live_account_discount,
            "applied_discount": applied_account_discount,
            "discount_source": discount_source,
            "meesho_prepaid_discount": applied_prepaid_discount,
            "offer_bucket": offer_bucket,
            "account_status": "Account Session Active",
            "account_verified": bool(acc_data.get("xo") or user_id or mobile)
        }

        # 3. Evaluate pricing for each pincode (concurrently for fast performance)
        from concurrent.futures import ThreadPoolExecutor

        def _eval_pin(pin: str) -> PriceBreakdown:
            return self._evaluate_single_pincode(
                product_id=product_id,
                pincode=pin,
                supplier_id=supplier_id,
                base_price=base_price,
                variation_id=chosen_var_id,
                variation_name=chosen_var_name,
                fod_discount=fod_discount,
                account_ident=acc_ident,
                live_discount=applied_account_discount if applied_account_discount > 0 else None,
                live_prepaid=applied_prepaid_discount if applied_prepaid_discount > 0 else None
            )

        if len(candidate_pins) > 1:
            with ThreadPoolExecutor(max_workers=min(6, len(candidate_pins))) as executor:
                results = list(executor.map(_eval_pin, candidate_pins))
        else:
            results = [_eval_pin(p) for p in candidate_pins]

        # 4. Rank results by final payable price (verified available first)
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
            account_context=account_context,
            results=ranked_results,
            cheapest_verified_option=cheapest,
            max_savings_vs_worst=max(0.0, savings)
        )

    def _evaluate_single_pincode(
        self,
        product_id: int,
        pincode: str,
        supplier_id: int,
        base_price: float,
        variation_id: Optional[int] = None,
        variation_name: Optional[str] = None,
        price_type: Optional[str] = None,
        fod_discount: float = 0.0,
        account_ident: Optional[str] = None,
        live_discount: Optional[float] = None,
        live_prepaid: Optional[float] = None
    ) -> PriceBreakdown:
        """Evaluates pricing breakdown for one pincode via direct shipping charge quote."""
        pin_str = str(pincode).strip()
        loc_meta = ALL_INDIA_PIN_MAP.get(pin_str, {})
        city = loc_meta.get("city", "")
        state = loc_meta.get("state", "")
        zone = loc_meta.get("zone", "")

        try:
            ship_info = check_shipping_charges_fast(
                self.client,
                dest_pin=pin_str,
                product_id=product_id,
                supplier_id=supplier_id,
                quantity=1
            )
        except Exception as e:
            return PriceBreakdown(
                product_id=product_id,
                pincode=pin_str,
                status=ComparisonStatus.FAILED,
                city=city,
                state=state,
                zone=zone,
                displayed_product_price=base_price,
                final_payable_price=base_price,
                error_message=f"Shipping query failed: {e}"
            )

        # Check if shipping was unserviceable or if charges lookup failed/timed out
        if not ship_info.get("serviceable", True) or ship_info.get("charges") is None or not ship_info.get("charges_verified", True):
            if not ship_info.get("serviceable", True) and ship_info.get("charges") is not None:
                st = ComparisonStatus.UNSUPPORTED_PINCODE
                msg = f"Pincode {pin_str} is not serviceable."
            else:
                st = ComparisonStatus.FAILED
                msg = ship_info.get("error") or f"Pincode {pin_str} shipping rate unverified (network or logistics timeout)."

            return PriceBreakdown(
                product_id=product_id,
                pincode=pin_str,
                status=st,
                city=city,
                state=state,
                zone=zone,
                displayed_product_price=base_price,
                delivery_charges=0.0,
                final_payable_price=base_price,
                error_message=msg,
                account_verified=False,
                raw_details=ship_info
            )

        delivery_charges = float(ship_info.get("charges") or 0.0)
        expected_date = ship_info.get("estimated_date") or ship_info.get("expected_delivery")

        # Account discount: prefer live Meesho quote, then FOD fallback
        if live_discount is not None:
            applied_account_discount = float(live_discount)
        elif fod_discount > 0:
            max_fod_cap = round(base_price * 0.585, 2)
            applied_account_discount = min(base_price, fod_discount, max_fod_cap)
        else:
            applied_account_discount = 0.0

        final_payable = max(0.0, base_price + delivery_charges - applied_account_discount)

        # Prepaid/UPI discount: prefer live Meesho quote, then standard estimate
        if live_prepaid is not None:
            prepaid_discount = float(live_prepaid)
        elif applied_account_discount > 0:
            prepaid_discount = round(min(30.0, max(0.0, base_price * 0.13)), 2)
        else:
            prepaid_discount = 0.0

        prepaid_payable = round(max(0.0, final_payable - prepaid_discount), 2) if prepaid_discount > 0 else None

        return PriceBreakdown(
            product_id=product_id,
            pincode=pin_str,
            status=ComparisonStatus.VERIFIED_AVAILABLE,
            city=city,
            state=state,
            zone=zone,
            displayed_product_price=round(base_price, 2),
            delivery_charges=round(delivery_charges, 2),
            eligible_discounts=round(applied_account_discount, 2),
            account_discount=round(applied_account_discount, 2),
            payment_method_discounts=0.0,
            final_payable_price=round(final_payable, 2),
            prepaid_payable_price=prepaid_payable,
            upi_verified_eligible=bool(prepaid_payable is not None),
            delivery_days=expected_date,
            account_identifier=account_ident,
            account_verified=bool(ship_info.get("account_specific", True)),
            raw_details=ship_info
        )
