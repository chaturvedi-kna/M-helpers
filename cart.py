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
    Queries product dynamic metadata, suppliers, inventory, and variations.
    """
    path = f"/api/3.0/product/dynamic?id={product_id}&context=search&origin=catalog_search_results"
    data = client.api_call("GET", path)
    prod = data.get("product") or {}
    suppliers = prod.get("suppliers") or []

    if not suppliers:
        raise ValueError(f"No active suppliers available for product ID {product_id}")

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
    payload = {
        "context": "pdp",
        "identifier": "cart",
        "cart_session": client.cart_session,
        "replaceable": False,
        "items": [
            {
                "identifier": "cart",
                "product_id": product_id,
                "supplier_id": supplier_id,
                "variation_id": variation_id,
                "variation": variation_name,
                "quantity": quantity,
                "selected_price_type_id": price_type,
                "client_metadata": None
            }
        ],
        "address_id": None,
        "user_id": client.account_data.get("user_id")
    }

    res = client.api_call("POST", "/api/1.0/cart/add", payload=payload)
    if res.get("success"):
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
    payload = {
        "context": "review",
        "identifier": "cart",
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
        "user_id": client.account_data.get("user_id")
    }
    return client.api_call("POST", "/api/9.0/cart", payload=payload)

