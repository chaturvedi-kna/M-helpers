"""
Pincode Optimization, Delivery Fee Minimization, and UPI Pricing Engine.
Finds optimal delivery charges across candidate pincodes and calculates final UPI prices.
"""

from typing import Dict, Any, List, Optional
from meesho_client import MeeshoClient


def check_shipping_charges_fast(
    client: MeeshoClient,
    dest_pin: str,
    product_id: int,
    supplier_id: int,
    quantity: int = 1
) -> Dict[str, Any]:
    """
    Lightweight check for shipping charges and serviceability for a specific product & supplier
    without mutating the current cart session.
    Never returns fake 0 delivery charges if Meesho shipping quote fails or times out.
    """
    pin_str = str(dest_pin).strip()
    params = {
        "dest_pin": pin_str,
        "product_id": int(product_id),
        "quantity": int(quantity),
        "supplier_id": int(supplier_id)
    }

    # 1. Primary authenticated / client-managed query with retry
    for attempt in range(2):
        try:
            res = client.api_call("GET", "/api/1.0/shipping", params=params)
            shipping = res.get("shipping") or res.get("shipping_2") or {}
            if shipping and ("charges" in shipping or "serviceable" in shipping):
                raw_charges = shipping.get("charges")
                charges_val = float(raw_charges) if raw_charges is not None else 0.0
                is_serv = shipping.get("serviceable", True)
                return {
                    "pincode": pin_str,
                    "serviceable": is_serv,
                    "charges": charges_val,
                    "charges_verified": True,
                    "expected_delivery": shipping.get("expected_delivery_time"),
                    "estimated_date": shipping.get("estimated_delivery_date"),
                    "account_specific": True
                }
        except Exception:
            pass

    # 2. Direct query fallback with complete mobile headers
    try:
        import uuid
        import requests
        import proxy_manager
        from config import DEFAULT_AUTHORIZATION, APP_VERSION, APP_VERSION_CODE, APP_SDK_VERSION
        from auth import build_meesho_headers

        if client and hasattr(client, "account_data") and client.account_data:
            sess_headers = build_meesho_headers(client.account_data)
            is_acc = True
        else:
            sess_headers = {
                "Authorization": DEFAULT_AUTHORIZATION,
                "App-Version": APP_VERSION,
                "App-Version-Code": APP_VERSION_CODE,
                "Instance-Id": uuid.uuid4().hex,
                "Country-Iso": "in",
                "Application-Id": "com.meesho.supply",
                "App-Session-Id": uuid.uuid4().hex,
                "App-Sdk-Version": APP_SDK_VERSION,
                "App-Client-Id": "android",
                "App-Iso-Language-Code": "en",
                "Meesho-User-Context": "anonymous",
                "User-Agent": "okhttp/4.9.0",
                "Accept": "application/json"
            }
            is_acc = False

        sess = requests.Session()
        sess.headers.update(sess_headers)
        r = proxy_manager.request_with_retry(
            sess,
            "GET",
            f"{client.api_host if client and hasattr(client, 'api_host') else 'https://prod.meeshoapi.com'}/api/1.0/shipping",
            params=params,
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            shipping = data.get("shipping") or data.get("shipping_2") or {}
            if shipping and ("charges" in shipping or "serviceable" in shipping):
                raw_charges = shipping.get("charges")
                charges_val = float(raw_charges) if raw_charges is not None else 0.0
                return {
                    "pincode": pin_str,
                    "serviceable": shipping.get("serviceable", True),
                    "charges": charges_val,
                    "charges_verified": True,
                    "expected_delivery": shipping.get("expected_delivery_time"),
                    "estimated_date": shipping.get("estimated_delivery_date"),
                    "account_specific": is_acc
                }
    except Exception:
        pass

    # 3. Explicit error result when logistics endpoint is unreachable or times out
    # DO NOT invent fake 0 charges; mark as unverified so ranking is never corrupted.
    return {
        "pincode": pin_str,
        "serviceable": False,
        "charges": None,
        "charges_verified": False,
        "expected_delivery": None,
        "estimated_date": None,
        "account_specific": False,
        "error": f"Shipping charges unavailable for pincode {pin_str} (network or logistics timeout)"
    }


def update_cart_location(
    client: MeeshoClient,
    dest_pin: str,
    address_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Updates cart destination pincode on /api/1.0/cart/location and fetches
    complete cart bill summary including the exact UPI plugin discount.
    """
    payload = {
        "context": "review",
        "identifier": "cart",
        "cart_session": client.cart_session or "",
        "dest_pin": str(dest_pin).strip(),
        "address_id": address_id,
        "customerAmount": None,
        "payment_modes": None,
        "replaceable": None,
        "item": None,
        "payment_instrument": None,
        "bank_offers": None,
        "filter_products": None,
        "is_self_pickup": None,
        "self_pickup_address": None,
        "is_emi": None,
        "user_id": client.account_data.get("user_id")
    }

    res = client.api_call("POST", "/api/1.0/cart/location", payload=payload)
    if res.get("success"):
        client.cart_session = res.get("cart_session", client.cart_session)

    result = res.get("result", {})
    addr = result.get("address", {})

    # Extract all pricing variations
    total = result.get("total", 0)
    effective_total = result.get("effective_total", 0)
    upi_price = result.get("effective_total_for_upi_plugin")
    if upi_price is None or upi_price == 0:
        # Fallback to effective_amount_all_payment or effective_total if UPI specific discount isn't populated
        upi_price = result.get("effective_amount_all_payment") or effective_total or total

    bnpl_price = result.get("effective_total_for_bnpl") or total

    # Delivery fee difference
    delivery_fee = 0
    # Meesho includes shipping fee inside items or as effective_total difference
    # In result, product_orders list has exact delivery charges if present
    product_orders = result.get("product_orders") or []
    for po in product_orders:
        delivery_fee += po.get("delivery_charge", 0) or po.get("shipping_charge", 0)

    return {
        "pincode": str(dest_pin),
        "serviceable": addr.get("pin_serviceable", True),
        "total": total,
        "effective_total": effective_total,
        "upi_price": upi_price,
        "bnpl_price": bnpl_price,
        "delivery_charges": delivery_fee,
        "city": addr.get("city", ""),
        "state": addr.get("state", ""),
        "raw_result": result
    }


def find_optimal_pincode_and_upi_price(
    client: MeeshoClient,
    candidate_pincodes: List[str],
    address_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Requirement 1: Iterates through candidate pincodes, evaluates shipping fees
    and cart prices, and returns the optimal delivery fee alongside final UPI price.
    """
    if not candidate_pincodes:
        raise ValueError("Candidate pincodes list cannot be empty.")

    client.log(f"[Optimizer] Evaluating {len(candidate_pincodes)} candidate pincodes for delivery fee & price optimization...")

    evaluations: List[Dict[str, Any]] = []

    for pin in candidate_pincodes:
        try:
            quote = update_cart_location(client, pin, address_id=address_id)
            if quote["serviceable"]:
                evaluations.append(quote)
                client.log(
                    f"  -> Pin {pin} ({quote['city']}): Total=₹{quote['total']}, "
                    f"Delivery=₹{quote['delivery_charges']}, Final UPI=₹{quote['upi_price']}"
                )
            else:
                client.log(f"  -> Pin {pin}: Unserviceable")
        except Exception as e:
            client.log(f"  -> Pin {pin} Error: {e}")

    if not evaluations:
        raise RuntimeError("None of the supplied candidate pincodes are serviceable for this cart.")

    # Sort primarily by lowest UPI price, then lowest delivery charges
    sorted_evals = sorted(evaluations, key=lambda x: (x["upi_price"], x["delivery_charges"]))
    best = sorted_evals[0]
    worst = sorted_evals[-1]

    savings = max(0, worst["upi_price"] - best["upi_price"])

    return {
        "best_pincode": best["pincode"],
        "best_city": best["city"],
        "best_state": best["state"],
        "final_upi_price": best["upi_price"],
        "delivery_charges": best["delivery_charges"],
        "standard_total": best["total"],
        "savings_vs_worst": savings,
        "all_evaluations": evaluations
    }

