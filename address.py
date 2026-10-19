"""
Address Management and Randomized Assignment for Meesho E2E Engine.
Handles pincode verification, listing addresses, and random address creation.
"""

import random
from typing import Dict, Any, List, Optional

from meesho_client import MeeshoClient


def verify_delivery_location(client: MeeshoClient, pincode: str) -> Dict[str, Any]:
    """
    Registers delivery location context and checks serviceability for a pincode.
    """
    payload = {
        "pincode": str(pincode).strip(),
        "context": "address_add_edit",
        "user_id": client.account_data.get("user_id")
    }
    res = client.api_call("POST", "/api/1.0/user/delivery-location", payload=payload)
    return res.get("user_delivery_location", {})


def get_account_addresses(client: MeeshoClient) -> List[Dict[str, Any]]:
    """Fetches all addresses registered on the account."""
    params = {
        "offset": 0,
        "limit": 50,
        "check_pin": "true",
        "context": "cart",
        "cart_identifier": "cart"
    }
    res = client.api_call("GET", "/api/3.0/addresses", params=params)
    return res.get("addresses", [])


def add_address(client: MeeshoClient, address_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adds a new delivery address to the account.
    """
    pincode = str(address_data.get("pin") or address_data.get("pincode")).strip()

    # Step 1: Pre-validate location with Meesho
    loc = verify_delivery_location(client, pincode)
    detected_city = loc.get("city") or address_data.get("city")

    mobile = str(address_data.get("mobile") or client.account_data.get("mobile") or "").replace("+91", "").strip()

    payload = {
        "name": address_data["name"],
        "mobile": mobile,
        "address_line_1": address_data["address_line_1"],
        "address_line_2": address_data.get("address_line_2", ""),
        "landmark": address_data.get("landmark", ""),
        "city": detected_city,
        "state": address_data["state"],
        "pin": pincode,
        "address_type": address_data.get("address_type", "Home"),
        "country_id": 1,
        "alternative_mobile": address_data.get("alternative_mobile")
    }

    client.log(f"[Address] Registering address for '{payload['name']}' at pincode {pincode} ({detected_city})...")
    res = client.api_call("POST", "/api/2.0/addresses", payload=payload)
    created = res.get("address", {})
    client.log(f"[Address] Successfully created address ID: {created.get('id')}")
    return created


def add_random_address_from_pool(
    client: MeeshoClient,
    address_pool: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Requirement 3: Randomly picks an address from the supplied pool
    and registers it on the account.
    """
    if not address_pool:
        raise ValueError("Address pool is empty.")

    chosen = random.choice(address_pool)
    client.log(f"[Address] Randomly picked address: {chosen.get('name')}, {chosen.get('city')} ({chosen.get('pin')})")
    return add_address(client, chosen)

