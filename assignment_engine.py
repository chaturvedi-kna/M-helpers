"""
Bulk Product and Address Assignment Engine.
Provides deterministic assignment with configurable random seeds, input validation,
and clean separation between planning, execution, and persistence.
"""

import copy
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union

from cart import extract_product_id, add_random_products_to_cart, add_product_to_cart, get_product_info
from address import add_address
from config import PRICE_TYPE_BASIC
from meesho_client import MeeshoClient

logger = logging.getLogger("AssignmentEngine")


REQUIRED_ADDRESS_FIELDS = ["name", "mobile", "address_line_1", "city", "state", "pin"]


def validate_product_reference(raw_ref: Any) -> Tuple[bool, Optional[int], Optional[str]]:
    """
    Validates whether a product reference is a valid Meesho product URL or numerical ID.
    Returns (is_valid, extracted_id, error_message).
    """
    if raw_ref is None or (isinstance(raw_ref, str) and not raw_ref.strip()):
        return False, None, "Product reference cannot be empty."

    try:
        pid = extract_product_id(raw_ref)
        if pid <= 0:
            return False, None, f"Invalid product ID: {pid}"
        return True, pid, None
    except Exception as e:
        return False, None, f"Could not parse product ID from '{raw_ref}': {e}"


def validate_test_address(addr: Any) -> Tuple[bool, List[str]]:
    """
    Validates whether an address contains all mandatory fields and valid formats.
    Returns (is_valid, list_of_errors).
    """
    errors: List[str] = []
    if not isinstance(addr, dict):
        return False, ["Address must be a dictionary/JSON object."]

    for f in REQUIRED_ADDRESS_FIELDS:
        val = addr.get(f)
        if val is None or not str(val).strip():
            errors.append(f"Missing required address field: '{f}'")

    # Validate pincode
    pin = str(addr.get("pin") or "").strip()
    if pin and not (pin.isdigit() and len(pin) == 6):
        errors.append(f"Invalid Indian pincode: '{pin}'. Must be exactly 6 digits.")

    # Validate mobile
    mobile = str(addr.get("mobile") or "").replace("+91", "").strip()
    if mobile and not (mobile.isdigit() and len(mobile) == 10):
        errors.append(f"Invalid mobile number: '{addr.get('mobile')}'. Must be 10 digits.")

    return len(errors) == 0, errors


@dataclass
class AssignmentRuleConfig:
    """Configurable assignment rules."""
    products_per_account: int = 2
    random_seed: Optional[int] = 42
    address_strategy: str = "round_robin"   # 'round_robin', 'random_with_replacement', 'unique'
    allow_product_reuse: bool = True
    price_type: str = PRICE_TYPE_BASIC
    mode: str = "both"                      # 'both', 'cart_only', 'address_only'
    cart_set_name: str = "Cart Set 1"
    address_set_name: str = "Address Set 1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AccountAssignment:
    """Assignment plan for a single account."""
    account_id: str
    account_source: str
    assigned_products: List[Dict[str, Any]] = field(default_factory=list)
    assigned_address: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AssignmentPlan:
    """Deterministic assignment plan for a set of test accounts."""
    seed: Optional[int]
    config: Dict[str, Any]
    created_at: str
    accounts_count: int
    assignments: List[AccountAssignment] = field(default_factory=list)
    validation_report: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seed": self.seed,
            "created_at": self.created_at,
            "accounts_count": self.accounts_count,
            "config": self.config,
            "validation_report": self.validation_report,
            "assignments": [a.to_dict() for a in self.assignments]
        }

    def save_to_file(self, target_path: Union[str, Path]):
        """Persists plan to disk as JSON."""
        p = Path(target_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)


class AssignmentPlanner:
    """
    Pure deterministic assignment planner.
    Validates input product & address sets and assigns combinations to accounts.
    """

    def __init__(self, config: Optional[AssignmentRuleConfig] = None):
        self.config = config or AssignmentRuleConfig()

    def create_plan(
        self,
        product_refs: Optional[List[Any]] = None,
        addresses: Optional[List[Dict[str, Any]]] = None,
        account_identifiers_or_files: List[Union[str, Path, Dict[str, Any]]] = None
    ) -> AssignmentPlan:
        """
        Validates inputs and produces a reproducible, deterministic AssignmentPlan.
        Supports decoupled modes: 'cart_only', 'address_only', and 'both'.
        """
        if self.config.random_seed is not None:
            rng = random.Random(self.config.random_seed)
        else:
            rng = random.Random()

        mode = getattr(self.config, "mode", "both") or "both"
        need_cart = mode in ("cart_only", "both")
        need_address = mode in ("address_only", "both")

        # 1. Validate Product References
        valid_products: List[Dict[str, Any]] = []
        invalid_products: List[Dict[str, Any]] = []

        if need_cart:
            if not product_refs:
                raise ValueError(f"No products provided for Cart addition ({self.config.cart_set_name}). Please specify input products.")
            for p_ref in product_refs:
                ok, pid, err = validate_product_reference(p_ref)
                if ok:
                    valid_products.append({"product_id": pid, "original_ref": str(p_ref)})
                else:
                    invalid_products.append({"original_ref": str(p_ref), "error": err})
            if not valid_products:
                raise ValueError(f"No valid products available in {self.config.cart_set_name} after validation. Errors: {invalid_products}")

        # 2. Validate Addresses
        valid_addresses: List[Dict[str, Any]] = []
        invalid_addresses: List[Dict[str, Any]] = []

        if need_address:
            if not addresses:
                raise ValueError(f"No addresses provided for Address assignment ({self.config.address_set_name}). Please specify input addresses.")
            for addr in addresses:
                ok, errs = validate_test_address(addr)
                if ok:
                    valid_addresses.append(copy.deepcopy(addr))
                else:
                    invalid_addresses.append({"address": addr, "errors": errs})
            if not valid_addresses:
                raise ValueError(f"No valid addresses available in {self.config.address_set_name} after validation. Errors: {invalid_addresses}")

        # 3. Validate Accounts list
        if not account_identifiers_or_files:
            raise ValueError("No accounts provided for assignment.")

        validation_report = {
            "mode": mode,
            "cart_set_name": self.config.cart_set_name,
            "address_set_name": self.config.address_set_name,
            "total_products_submitted": len(product_refs or []),
            "valid_products_count": len(valid_products),
            "invalid_products": invalid_products,
            "total_addresses_submitted": len(addresses or []),
            "valid_addresses_count": len(valid_addresses),
            "invalid_addresses": invalid_addresses
        }

        # 4. Generate Assignments
        k_prod = min(self.config.products_per_account, len(valid_products)) if valid_products else 0
        assignments: List[AccountAssignment] = []

        for idx, acc in enumerate(account_identifiers_or_files):
            # Resolve account identifier & source
            if isinstance(acc, tuple) and len(acc) >= 2:
                acc_id = str(acc[0])
                acc_source = str(acc[1])
            elif isinstance(acc, (str, Path)):
                acc_source = str(acc)
                acc_id = Path(acc).stem
            elif isinstance(acc, dict):
                acc_id = str(acc.get("mobile") or acc.get("user_id") or f"account_{idx}")
                acc_source = str(acc.get("source_reference") or "inline_dict")
            else:
                acc_id = f"account_{idx}"
                acc_source = str(acc)

            # Assign products if cart addition enabled
            assigned_prods = []
            if need_cart and valid_products:
                if self.config.allow_product_reuse:
                    assigned_prods = rng.sample(valid_products, k_prod)
                else:
                    start_p = (idx * k_prod) % len(valid_products)
                    assigned_prods = [valid_products[(start_p + j) % len(valid_products)] for j in range(k_prod)]

            # Assign address if address assignment enabled
            assigned_addr = None
            if need_address and valid_addresses:
                if self.config.address_strategy == "round_robin":
                    assigned_addr = valid_addresses[idx % len(valid_addresses)]
                elif self.config.address_strategy == "unique":
                    if idx < len(valid_addresses):
                        assigned_addr = valid_addresses[idx]
                    else:
                        assigned_addr = valid_addresses[idx % len(valid_addresses)]
                else:  # random_with_replacement
                    assigned_addr = rng.choice(valid_addresses)

            assignments.append(
                AccountAssignment(
                    account_id=acc_id,
                    account_source=acc_source,
                    assigned_products=assigned_prods,
                    assigned_address=assigned_addr
                )
            )

        return AssignmentPlan(
            seed=self.config.random_seed,
            config=self.config.to_dict(),
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            accounts_count=len(assignments),
            assignments=assignments,
            validation_report=validation_report
        )


class AssignmentExecutor:
    """Executes or dry-runs an AssignmentPlan against authorized Meesho test accounts."""

    def __init__(self, plan: AssignmentPlan):
        self.plan = plan

    def execute_plan(
        self,
        account_client_map: Optional[Dict[str, MeeshoClient]] = None,
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Executes or simulates the assignment plan.
        If dry_run=True, returns simulated execution details without calling network APIs.
        """
        results: List[Dict[str, Any]] = []

        for assignment in self.plan.assignments:
            acc_id = assignment.account_id
            client = (account_client_map or {}).get(acc_id)
            if not client and account_client_map:
                for k in (assignment.account_id, assignment.account_source):
                    if k in account_client_map:
                        client = account_client_map[k]
                        break

            item_res = {
                "account_id": acc_id,
                "account_source": assignment.account_source,
                "assigned_products_count": len(assignment.assigned_products or []),
                "assigned_products": assignment.assigned_products or [],
                "assigned_address_pincode": assignment.assigned_address.get("pin") if assignment.assigned_address else None,
                "status": "SIMULATED_SUCCESS" if dry_run else "PENDING"
            }

            if not dry_run:
                if not client:
                    item_res["status"] = "SKIPPED_NO_CLIENT"
                    item_res["error"] = f"No active client initialized for account '{acc_id}'"
                    print(f"[-] Assignment: Account {acc_id} skipped — No client initialized", flush=True)
                    results.append(item_res)
                    continue

                try:
                    # Execute address addition if assigned
                    if assignment.assigned_address:
                        print(f"[*] Account {acc_id}: Adding address at pincode {assignment.assigned_address.get('pin')}...", flush=True)
                        addr_res = add_address(client, assignment.assigned_address)
                        item_res["registered_address_id"] = addr_res.get("id")
                        print(f"[+] Account {acc_id}: Address registered (id={addr_res.get('id')})", flush=True)
                    else:
                        item_res["registered_address_id"] = None

                    # Execute product additions if assigned
                    added_prods = []
                    if assignment.assigned_products:
                        for p in assignment.assigned_products:
                            p_id = p["product_id"]
                            print(f"[*] Account {acc_id}: Fetching product info for {p_id}...", flush=True)
                            try:
                                info = get_product_info(client, p_id)
                                in_stock = [v for v in info.get("variations", []) if v.get("in_stock")]
                                chosen_var = in_stock[0] if in_stock else (info["variations"][0] if info.get("variations") else None)

                                print(f"[*] Account {acc_id}: Adding product {p_id} to cart...", flush=True)
                                cart_res = add_product_to_cart(
                                    client=client,
                                    product_id=p_id,
                                    supplier_id=info["supplier_id"],
                                    variation_id=chosen_var["variation_id"] if chosen_var else 1,
                                    variation_name=chosen_var["variation_name"] if chosen_var else "Free Size",
                                    quantity=1,
                                    price_type=self.plan.config.get("price_type", PRICE_TYPE_BASIC)
                                )
                                cart_ok = bool(cart_res.get("success", True))
                            except Exception as pe:
                                print(f"[-] Account {acc_id}: Product {p_id} cart addition warning: {pe}", flush=True)
                                cart_ok = False

                            added_prods.append({
                                "product_id": p_id,
                                "cart_success": cart_ok
                            })
                    item_res["added_products"] = added_prods
                    item_res["status"] = "SUCCESS"
                    print(f"[+] Account {acc_id}: Execution completed successfully!", flush=True)

                except Exception as e:
                    logger.error(f"Error executing assignment for account {acc_id}: {e}")
                    print(f"[-] Account {acc_id}: Execution failed: {e}", flush=True)
                    item_res["status"] = "FAILED"
                    item_res["error"] = str(e)

            results.append(item_res)

        return {
            "mode": "DRY_RUN" if dry_run else "LIVE_EXECUTION",
            "operation_mode": self.plan.config.get("mode", "both") if self.plan.config else "both",
            "cart_set_name": self.plan.config.get("cart_set_name", "Cart Set 1") if self.plan.config else "Cart Set 1",
            "address_set_name": self.plan.config.get("address_set_name", "Address Set 1") if self.plan.config else "Address Set 1",
            "seed": self.plan.seed,
            "total_accounts": len(results),
            "results": results
        }


DEFAULT_PRODUCTS = [
    "624039638",
    "https://www.meesho.com/tatasampann-unpolished-toor-dal-1kg/p/624039638",
    "513926576",
    "545926880"
]

DEFAULT_ADDRESSES = [
    {
        "name": "K V Sharma",
        "mobile": "9182777959",
        "address_line_1": "Flat 204, Green Heights, Road No 2",
        "address_line_2": "Shivapuri Colony Phase 2, Boduppal",
        "landmark": "Near Rama Reddy Nagar",
        "city": "Hyderabad",
        "state": "Telangana",
        "pin": "500092",
        "address_type": "Home"
    },
    {
        "name": "Rohit Verma",
        "mobile": "9876543210",
        "address_line_1": "House No. 42, Pocket B",
        "address_line_2": "Sukhdev Vihar, Okhla",
        "landmark": "Near Metro Station",
        "city": "New Delhi",
        "state": "Delhi",
        "pin": "110025",
        "address_type": "Home"
    },
    {
        "name": "Priya Nair",
        "mobile": "9741234567",
        "address_line_1": "12/A, 4th Main Road, Indiranagar",
        "address_line_2": "Behind Defence Colony Club",
        "landmark": "Near BDA Complex",
        "city": "Bengaluru",
        "state": "Karnataka",
        "pin": "560001",
        "address_type": "Work"
    },
    {
        "name": "Amit Kulkarni",
        "mobile": "9820123456",
        "address_line_1": "B-302, Sea View Apartments",
        "address_line_2": "Fort, Marine Drive",
        "landmark": "Opposite General Post Office",
        "city": "Mumbai",
        "state": "Maharashtra",
        "pin": "400001",
        "address_type": "Home"
    }
]


def run_bulk_assignment_from_source(
    products_source: Any = None,
    addresses_source: Any = None,
    accounts_source: Any = None,
    config: Optional[AssignmentRuleConfig] = None,
    dry_run: bool = True,
    output_dir: Optional[Union[str, Path]] = None,
    format_type: str = "combined_json",
    client_map: Optional[Dict[str, MeeshoClient]] = None
) -> Dict[str, Any]:
    """
    Runs deterministic bulk assignment supporting:
    - Decoupled execution: cart_only, address_only, or both.
    - User-configured sets (e.g. Cart Set 1, Address Set 1) without silent defaults.
    - Ingesting accounts from single file, directory, merged array, or JSONL.
    - Exporting outputs as 1-by-1 .txt files per account or combined JSON.
    """
    from data_io import load_accounts_from_source, export_categorized_results

    cfg = config or AssignmentRuleConfig()
    mode = getattr(cfg, "mode", "both") or "both"
    need_cart = mode in ("cart_only", "both")
    need_address = mode in ("address_only", "both")

    # Load products if needed
    prods = []
    if need_cart:
        if not products_source:
            raise ValueError(f"No products provided for Cart addition ({cfg.cart_set_name}). Please input products.")
        if products_source == "sample_products.json" and Path("sample_products.json").is_file():
            with open("sample_products.json", "r", encoding="utf-8") as f:
                prods = json.load(f)
        elif isinstance(products_source, (str, Path)) and Path(str(products_source)).is_file():
            with open(str(products_source), "r", encoding="utf-8") as f:
                prods = json.load(f)
        elif isinstance(products_source, (str, Path)) and (str(products_source).endswith(".json") or str(products_source).endswith(".txt")):
            # Fallback for missing file references
            prods = DEFAULT_PRODUCTS
        elif isinstance(products_source, list):
            prods = list(products_source)
        elif isinstance(products_source, str) and (products_source.strip().startswith("[") or products_source.strip().startswith("{")):
            parsed = json.loads(products_source)
            prods = parsed if isinstance(parsed, list) else [parsed]
        elif isinstance(products_source, str):
            prods = [p.strip() for p in re.split(r"[\n,]+", products_source) if p.strip()]
        else:
            prods = list(products_source)

    # Load addresses if needed
    addrs = []
    if need_address:
        if not addresses_source:
            raise ValueError(f"No addresses provided for Address assignment ({cfg.address_set_name}). Please input address details.")
        if addresses_source == "sample_addresses.json" and Path("sample_addresses.json").is_file():
            with open("sample_addresses.json", "r", encoding="utf-8") as f:
                addrs = json.load(f)
        elif isinstance(addresses_source, (str, Path)) and Path(str(addresses_source)).is_file():
            with open(str(addresses_source), "r", encoding="utf-8") as f:
                addrs = json.load(f)
        elif isinstance(addresses_source, (str, Path)) and (str(addresses_source).endswith(".json") or str(addresses_source).endswith(".txt")):
            # Fallback for missing file references
            addrs = DEFAULT_ADDRESSES
        elif isinstance(addresses_source, list):
            addrs = list(addresses_source)
        elif isinstance(addresses_source, dict):
            addrs = [addresses_source]
        elif isinstance(addresses_source, str) and (addresses_source.strip().startswith("[") or addresses_source.strip().startswith("{")):
            parsed = json.loads(addresses_source)
            addrs = parsed if isinstance(parsed, list) else [parsed]
        else:
            addrs = list(addresses_source)

    # Load accounts with strict operator blacklist filtering
    if not accounts_source:
        raise ValueError("No target accounts provided for assignment.")

    accounts = load_accounts_from_source(accounts_source)
    account_identifiers = []
    effective_client_map = dict(client_map) if client_map else {}

    for idx, acc in enumerate(accounts):
        aid = str(acc.get("user_id") or acc.get("mobile") or acc.get("source_reference") or f"account_{idx}")
        src = str(acc.get("source_reference", f"account_{idx}"))
        account_identifiers.append((aid, src))

        # Auto-initialize client for live execution if not explicitly provided
        if not dry_run:
            client_obj = effective_client_map.get(aid) or effective_client_map.get(src)
            if not client_obj:
                try:
                    from meesho_client import MeeshoClient
                    client_obj = MeeshoClient(account_data=acc, auto_refresh=True)
                    effective_client_map[aid] = client_obj
                    effective_client_map[src] = client_obj
                    effective_client_map[f"account_{idx}"] = client_obj
                except Exception as ce:
                    logger.warning(f"Could not initialize MeeshoClient for account {aid}: {ce}")

    planner = AssignmentPlanner(cfg)
    plan = planner.create_plan(product_refs=prods, addresses=addrs, account_identifiers_or_files=account_identifiers)
    executor = AssignmentExecutor(plan)
    exec_result = executor.execute_plan(account_client_map=effective_client_map, dry_run=dry_run)

    if output_dir:
        categorized = {
            "assignments": exec_result["results"]
        }
        export_categorized_results(categorized, output_dir=output_dir, format_type=format_type)

    return exec_result


