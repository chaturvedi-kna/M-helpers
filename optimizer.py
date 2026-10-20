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
    """
    pin_str = str(dest_pin).strip()
    params = {
        "dest_pin": pin_str,
        "product_id": int(product_id),
        "quantity": int(quantity),
        "supplier_id": int(supplier_id)
    }

    try:
        res = client.api_call("GET", "/api/1.0/shipping", params=params)
        shipping = res.get("shipping") or res.get("shipping_2") or {}
        if shipping and ("charges" in shipping or "serviceable" in shipping):
            return {
                "pincode": pin_str,
                "serviceable": shipping.get("serviceable", True),
                "charges": float(shipping.get("charges") or 0.0),
                "expected_delivery": shipping.get("expected_delivery_time"),
                "estimated_date": shipping.get("estimated_delivery_date"),
                "account_specific": True
            }
    except Exception:
        pass

    # Direct query fallback using client's session headers or anonymous headers
    try:
        import requests
        import proxy_manager
        from auth import build_meesho_headers

        if client and hasattr(client, "account_data") and client.account_data:
            sess_headers = build_meesho_headers(client.account_data)
            is_acc = True
        else:
            sess_headers = {
                "Authorization": "32c4d8137cn9eb493a1921f203173080",
                "App-Version": "29.6",
                "App-Version-Code": "870",
                "Country-Iso": "in",
                "Application-Id": "com.meesho.supply",
                "App-Client-Id": "android",
                "Meesho-User-Context": "anonymous",
                "User-Agent": "okhttp/4.9.0"
            }
            is_acc = False

        sess = requests.Session()
        sess.headers.update(sess_headers)
        r = proxy_manager.request_with_retry(sess, "GET", "https://prod.meeshoapi.com/api/1.0/shipping", params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            shipping = data.get("shipping") or data.get("shipping_2") or {}
            if shipping:
                return {
                    "pincode": pin_str,
                    "serviceable": shipping.get("serviceable", True),
                    "charges": float(shipping.get("charges") or 0.0),
                    "expected_delivery": shipping.get("expected_delivery_time"),
                    "estimated_date": shipping.get("estimated_delivery_date"),
                    "account_specific": is_acc
                }
    except Exception:
        pass
    except Exception:
        pass

    # Fallback to delivery-location verification
    try:
        from address import verify_delivery_location
        loc = verify_delivery_location(client, pin_str)
        if loc and (loc.get("city") or loc.get("pincode")):
            return {
                "pincode": pin_str,
                "serviceable": True,
                "charges": 0,
                "expected_delivery": "Standard Delivery",
                "estimated_date": None
            }
    except Exception:
        pass

    # Postal verification fallback
    is_valid_pin = bool(len(pin_str) == 6 and pin_str.isdigit() and pin_str[0] in "123456789")
    return {
        "pincode": pin_str,
        "serviceable": is_valid_pin,
        "charges": 0,
        "expected_delivery": "Standard Delivery" if is_valid_pin else None,
        "estimated_date": None
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

