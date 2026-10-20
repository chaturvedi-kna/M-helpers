"""
Cart Operations and Product Management for Meesho E2E Engine.
Handles product link parsing, variation discovery, and randomized cart addition.
"""

import random
import re
import time
from typing import Dict, Any, List, Optional, Union
import requests

from meesho_client import MeeshoClient
from config import PRICE_TYPE_BASIC, PRICE_TYPE_ALL


def extract_product_id(link_or_id: Union[str, int]) -> int:
    """
    Extracts numerical Meesho product ID from direct IDs, product links, or short links.
    Examples:
    - 624039638 -> 624039638
    - "https://www.meesho.com/tata-sampann-dal/p/624039638" -> 624039638
    - "https://www.meesho.com/s/p/101abc" -> resolves redirect and extracts ID
    """
    raw = str(link_or_id).strip()

    # Case 1: Pure digits
    if raw.isdigit():
        return int(raw)

    # Case 2: Standard Meesho URL containing /p/<id>
    match = re.search(r"/p/(\d+)", raw)
    if match:
        return int(match.group(1))

    # Case 3: Query param or digits at the end
    match = re.search(r"(\d{6,12})", raw)
    if match:
        return int(match.group(1))

    # Case 4: Short link (e.g. /s/p/...) - follow redirect head
    if "meesho.com" in raw:
        try:
            resp = requests.head(raw, allow_redirects=True, timeout=10)
            final_url = resp.url
            match = re.search(r"/p/(\d+)", final_url)
            if match:
                return int(match.group(1))
        except Exception:
            pass

    raise ValueError(f"Could not extract numerical product ID from: {link_or_id}")


def get_product_info(client: MeeshoClient, product_id: int) -> Dict[str, Any]:
    """
    Queries product metadata, suppliers, inventory, and variations.
    Uses static endpoint (/api/3.0/product/static) first for reliable direct lookup,
    falling back to dynamic endpoint or anonymous requests.
    """
    paths_to_try = [
        f"/api/3.0/product/static?id={product_id}&context=search&ad_active=true",
        f"/api/3.0/product/dynamic?id={product_id}&context=search&origin=catalog_search_results"
    ]
    data = {}
    last_err = None
    for path in paths_to_try:
        try:
            res = client.api_call("GET", path)
            if res and res.get("product") and res.get("product", {}).get("suppliers"):
                data = res
                break
            elif res and res.get("product"):
                data = res
        except Exception as e:
            last_err = e
            # Try anonymous request for this path
            from config import DEFAULT_AUTHORIZATION, APP_VERSION, APP_VERSION_CODE, APP_SDK_VERSION
            import uuid
            import proxy_manager

            anon_headers = {
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
                "X-Wishlist-Aggregation-Required": "false",
                "Accept": "application/json",
                "User-Agent": "okhttp/4.9.0",
            }
            anon_sess = requests.Session()
            anon_sess.headers.update(anon_headers)
            url = f"{client.api_host}{path}"
            try:
                r = proxy_manager.request_with_retry(
                    session=anon_sess,
                    method="GET",
                    url=url,
                    timeout=12
                )
                if r.status_code == 200:
                    cand = r.json()
                    if cand.get("product"):
                        data = cand
                        if cand.get("product", {}).get("suppliers"):
                            break
            except Exception as anon_e:
                last_err = anon_e

    prod = data.get("product") or {}
    suppliers = prod.get("suppliers") or []

    if not suppliers:
        raise ValueError(f"No active suppliers available for product ID {product_id} (last error: {last_err})")

    # Pick the cheapest or primary supplier
    supplier = sorted(suppliers, key=lambda s: s.get("price", float("inf")))[0]
    inventory = supplier.get("inventory") or []

    variations = []
    for item in inventory:
        var = item.get("variation") or {}
        variations.append({
            "variation_id": var.get("id"),
            "variation_name": var.get("name") or "Free Size",
            "in_stock": item.get("in_stock", True),
            "mrp": item.get("mrp"),
            "price": supplier.get("price")
        })

    return {
        "product_id": product_id,
        "name": prod.get("name"),
        "supplier_id": supplier.get("id"),
        "supplier_name": supplier.get("name"),
        "price": supplier.get("price"),
        "variations": variations
    }


def add_product_to_cart(
    client: MeeshoClient,
    product_id: int,
    supplier_id: int,
    variation_id: int,
    variation_name: str = "Free Size",
    quantity: int = 1,
    price_type: str = PRICE_TYPE_BASIC
) -> Dict[str, Any]:
    """
    Adds a specific product variation and supplier to cart.
    """
    user_id = client.account_data.get("user_id")
    if not user_id and client.account_data.get("xo"):
        try:
            from account_manager import parse_xo_token
            meta = parse_xo_token(client.account_data.get("xo"))
            if meta.user_id:
                user_id = meta.user_id
                client.account_data["user_id"] = user_id
        except Exception:
            pass

    if user_id is not None:
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            pass

    payload = {
        "context": "pdp",
        "identifier": "buy_now",
        "cart_session": client.cart_session,
        "replaceable": False,
        "items": [
            {
                "identifier": "buy_now",
                "product_id": int(product_id),
                "supplier_id": int(supplier_id),
                "variation_id": int(variation_id),
                "variation": variation_name,
                "quantity": int(quantity),
                "selected_price_type_id": price_type or PRICE_TYPE_BASIC,
                "client_metadata": None
            }
        ],
        "address_id": None,
        "user_id": user_id
    }

    res = client.api_call("POST", "/api/1.0/cart/add", payload=payload)
    if res and res.get("success"):
        client.cart_session = res.get("cart_session")
        client.log(f"[Cart] Added product {product_id} ({variation_name}) to cart. Session updated.")
    return res


def add_random_products_to_cart(
    client: MeeshoClient,
    product_links_or_ids: List[Union[str, int]],
    count: int = 1,
    price_type: str = PRICE_TYPE_BASIC,
    jitter_range: tuple = (1.0, 2.5)
) -> List[Dict[str, Any]]:
    """
    Requirement 2: Selects a random subset of products from the given links/IDs
    and adds them to cart.
    """
    if not product_links_or_ids:
        raise ValueError("Product list is empty.")

    # Randomly sample 'count' products
    k = min(count, len(product_links_or_ids))
    sampled = random.sample(product_links_or_ids, k)
    client.log(f"[Cart] Randomly selected {k} products out of {len(product_links_or_ids)} to add.")

    added_items = []
    for item in sampled:
        product_id = extract_product_id(item)
        info = get_product_info(client, product_id)

        # Pick random in-stock variation (or first if Free Size)
        in_stock = [v for v in info["variations"] if v["in_stock"]]
        chosen_var = random.choice(in_stock) if in_stock else info["variations"][0]

        res = add_product_to_cart(
            client=client,
            product_id=product_id,
            supplier_id=info["supplier_id"],
            variation_id=chosen_var["variation_id"],
            variation_name=chosen_var["variation_name"],
            quantity=1,
            price_type=price_type
        )

        added_items.append({
            "product_id": product_id,
            "product_name": info["name"],
            "supplier_id": info["supplier_id"],
            "variation": chosen_var["variation_name"],
            "cart_result": res.get("result", {})
        })

        # Anti-bot jitter delay
        time.sleep(random.uniform(*jitter_range))

    return added_items


def view_cart(client: MeeshoClient) -> Dict[str, Any]:
    """Fetches full cart details and breakdown from /api/9.0/cart."""
    user_id = client.account_data.get("user_id")
    if not user_id and client.account_data.get("xo"):
        try:
            from account_manager import parse_xo_token
            meta = parse_xo_token(client.account_data.get("xo"))
            if meta.user_id:
                user_id = meta.user_id
                client.account_data["user_id"] = user_id
        except Exception:
            pass

    if user_id is not None:
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            pass

    payload = {
        "context": "review",
        "identifier": "buy_now",
        "cart_session": client.cart_session or "",
        "dest_pin": None,
        "address_id": None,
        "customerAmount": None,
        "payment_modes": None,
        "replaceable": None,
        "item": None,
        "payment_instrument": None,
        "bank_offers": None,
        "filter_products": True,
        "is_self_pickup": None,
        "self_pickup_address": None,
        "is_emi": None,
        "user_id": user_id
    }
    return client.api_call("POST", "/api/9.0/cart", payload=payload)

