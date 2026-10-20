"""
Account Classification Engine for Meesho JSON Records.
Classifies account records based on order history into fresh, used, or unknown.
Ensures records with missing, malformed, or incomplete order data are strictly
classified as 'unknown' rather than incorrectly assumed fresh.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union

logger = logging.getLogger("AccountClassifier")


class AccountClassification(str, Enum):
    FRESH = "fresh"
    USED = "used"
    UNKNOWN = "unknown"


@dataclass
class AccountRecordResult:
    """Individual classification outcome for an account JSON file."""
    source_file: str
    classification: AccountClassification
    order_count: Optional[int] = None
    reason: str = ""
    is_valid_schema: bool = True
    validation_errors: List[str] = field(default_factory=list)
    account_id: Optional[str] = None
    mobile: Optional[str] = None
    is_refreshed: bool = False
    raw_record: Optional[Dict[str, Any]] = None

    def to_dict(self, include_raw: bool = False) -> Dict[str, Any]:
        d = {
            "source_file": self.source_file,
            "account_id": self.account_id,
            "mobile": self.mobile,
            "classification": self.classification.value,
            "order_count": self.order_count,
            "reason": self.reason,
            "is_valid_schema": self.is_valid_schema,
            "is_refreshed": self.is_refreshed,
            "validation_errors": self.validation_errors
        }
        if include_raw and self.raw_record is not None:
            d["raw_record"] = self.raw_record
        return d


@dataclass
class ClassificationSummary:
    """Summary of bulk account classification run."""
    total_scanned: int
    fresh_count: int
    used_count: int
    unknown_count: int
    invalid_files_count: int
    fresh_accounts: List[AccountRecordResult] = field(default_factory=list)
    used_accounts: List[AccountRecordResult] = field(default_factory=list)
    unknown_accounts: List[AccountRecordResult] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def _clean_record(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not raw or not isinstance(raw, dict):
            return {}
        rec = dict(raw)
        rec.pop("_is_refreshed", None)
        return rec

    def get_fresh_account_records(self) -> List[Dict[str, Any]]:
        """Returns array of fresh account dicts (refreshed if updated, else original)."""
        return [self._clean_record(r.raw_record) for r in self.fresh_accounts if r.raw_record]

    def get_used_account_records(self) -> List[Dict[str, Any]]:
        """Returns array of used account dicts (refreshed if updated, else original)."""
        return [self._clean_record(r.raw_record) for r in self.used_accounts if r.raw_record]

    def get_unknown_account_records(self) -> List[Dict[str, Any]]:
        """Returns array of unknown account dicts."""
        return [self._clean_record(r.raw_record) for r in self.unknown_accounts if r.raw_record]

    def get_combined_account_records(self) -> List[Dict[str, Any]]:
        """Returns array of all classified account dicts combined."""
        all_recs = self.fresh_accounts + self.used_accounts + self.unknown_accounts
        return [self._clean_record(r.raw_record) for r in all_recs if r.raw_record]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": {
                "total_scanned": self.total_scanned,
                "fresh_count": self.fresh_count,
                "used_count": self.used_count,
                "unknown_count": self.unknown_count,
                "invalid_files_count": self.invalid_files_count
            },
            "fresh_files": [r.source_file for r in self.fresh_accounts],
            "used_files": [r.source_file for r in self.used_accounts],
            "unknown_files": [r.source_file for r in self.unknown_accounts],
            "detailed_results": [
                r.to_dict() for r in (self.fresh_accounts + self.used_accounts + self.unknown_accounts)
            ],
            "fresh_accounts": self.get_fresh_account_records(),
            "used_accounts": self.get_used_account_records(),
            "combined_accounts": self.get_combined_account_records(),
            "errors": self.errors
        }


def extract_order_history_info(data: Dict[str, Any]) -> Tuple[Optional[int], str, bool]:
    """
    Inspects account JSON for explicit, complete order history information.
    Returns (order_count, explanation, is_complete).
    If order history is missing, ambiguous, or incomplete, is_complete will be False.
    """
    # Case 1: Explicit 'orders' list
    if "orders" in data:
        orders_val = data["orders"]
        if isinstance(orders_val, list):
            return len(orders_val), f"Explicit 'orders' list found with {len(orders_val)} entries", True
        elif orders_val is None:
            return None, "Field 'orders' is null/None", False
        else:
            return None, f"Field 'orders' has unexpected type: {type(orders_val).__name__}", False

    # Case 2: Explicit 'order_history' field
    if "order_history" in data:
        oh_val = data["order_history"]
        if isinstance(oh_val, list):
            return len(oh_val), f"Explicit 'order_history' list found with {len(oh_val)} entries", True
        elif isinstance(oh_val, dict):
            if "total" in oh_val and isinstance(oh_val["total"], int):
                return oh_val["total"], f"Explicit 'order_history.total' count is {oh_val['total']}", True
            if "orders" in oh_val and isinstance(oh_val["orders"], list):
                return len(oh_val["orders"]), f"Explicit 'order_history.orders' list with {len(oh_val['orders'])} entries", True
        return None, "Field 'order_history' is malformed or incomplete", False

    # Case 3: Explicit numeric count field
    for count_key in ("order_count", "total_orders", "orders_count", "orders_total"):
        if count_key in data:
            val = data[count_key]
            if isinstance(val, int) and val >= 0:
                return val, f"Explicit '{count_key}' count is {val}", True
            if isinstance(val, str) and val.strip().isdigit():
                return int(val.strip()), f"Explicit '{count_key}' count is {val.strip()}", True
            return None, f"Field '{count_key}' is not a valid non-negative integer", False

    # Case 4: Other list formats
    for list_key in ("orders_list", "order_list", "sub_orders", "sub_order_list"):
        if list_key in data and isinstance(data[list_key], list):
            return len(data[list_key]), f"Found '{list_key}' with {len(data[list_key])} entries", True

    # Order history information is completely absent
    return None, "Order history information is missing from the record", False


def fetch_live_order_count(
    account_data: Dict[str, Any],
    api_host: str = "https://prod.meeshoapi.com",
    auto_refresh: bool = True
) -> Tuple[Optional[int], str, bool]:
    """
    Directly queries Meesho's authenticated order history API (/api/3.0/user/orders)
    to determine live order count for the account. Automatically refreshes expired
    XO tokens using Direct API and Telegram bot fallback.
    Returns (order_count, explanation, is_complete).
    """
    import requests
    from auth import build_meesho_headers, refresh_account_via_telegram
    from account_manager import DirectApiTokenRefreshProvider, is_token_expired

    user_id = account_data.get("user_id")
    if not user_id:
        return None, "Missing user_id for live orders query", False

    curr_acc = dict(account_data)
    refreshed_flag = False

    def _refresh(acc):
        try:
            prov = DirectApiTokenRefreshProvider(api_host=api_host)
            return prov.refresh(acc)
        except Exception as de:
            logger.info(f"Direct API refresh failed ({de}), trying Telegram fallback...")
            return refresh_account_via_telegram(acc)

    if auto_refresh and is_token_expired(curr_acc):
        try:
            curr_acc = _refresh(curr_acc)
            refreshed_flag = True
            account_data.update(curr_acc)
            account_data["_is_refreshed"] = True
        except Exception as e:
            logger.warning(f"Failed to auto-refresh token before live orders query: {e}")

    headers = build_meesho_headers(curr_acc)
    url = f"{api_host.rstrip('/')}/api/3.0/user/orders"
    payload = {
        "limit": 20,
        "filters": {},
        "user_id": int(user_id)
    }

    try:
        import proxy_manager
        sess = requests.Session()
        sess.headers.update(headers)
        r = proxy_manager.request_with_retry(
            session=sess,
            method="POST",
            url=url,
            account_key=str(user_id),
            json=payload,
            timeout=15
        )
        if r.status_code == 401 and auto_refresh:
            # Retry once after explicit refresh
            try:
                curr_acc = _refresh(curr_acc)
                refreshed_flag = True
                account_data.update(curr_acc)
                account_data["_is_refreshed"] = True
                headers = build_meesho_headers(curr_acc)
                sess.headers.clear()
                sess.headers.update(headers)
                r = proxy_manager.request_with_retry(
                    session=sess,
                    method="POST",
                    url=url,
                    account_key=str(user_id),
                    json=payload,
                    timeout=15
                )
            except Exception as ref_err:
                logger.warning(f"Live orders token refresh on 401 failed: {ref_err}")
                return None, f"Live Meesho API authentication failed (HTTP 401): {ref_err}", False

        if r.status_code == 200:
            orders_data = r.json()
            sub_orders = orders_data.get("sub_order_list") or orders_data.get("orders") or orders_data.get("order_list") or []
            total_orders = orders_data.get("total") or orders_data.get("count")
            if total_orders is not None and isinstance(total_orders, int):
                count = total_orders
            else:
                count = len(sub_orders)
            return count, f"Live Meesho API query confirmed {count} total order(s)", True
        else:
            return None, f"Live Meesho API returned HTTP {r.status_code}: {r.text[:120]}", False
    except Exception as e:
        return None, f"Live Meesho API query failed: {e}", False


def classify_account_data(
    data: Any,
    source_reference: str = "<inline>",
    live: bool = False
) -> AccountRecordResult:
    """
    Parses and classifies a single account record.
    Ensures missing/incomplete order history records are categorized as UNKNOWN.
    If live=True and order history is missing, queries live Meesho orders API.
    """
    if not isinstance(data, dict):
        return AccountRecordResult(
            source_file=source_reference,
            classification=AccountClassification.UNKNOWN,
            is_valid_schema=False,
            validation_errors=["Record is not a valid JSON dictionary."],
            reason="Invalid JSON format: expected object."
        )

    # Basic schema check
    errors: List[str] = []
    user_id = str(data.get("user_id") or "")
    mobile = str(data.get("mobile") or data.get("phone") or "")
    if not user_id and not mobile:
        errors.append("Missing both 'user_id' and 'mobile'/'phone' identification fields.")

    if not data.get("xo"):
        errors.append("Missing composite token 'xo'.")

    is_valid_schema = len(errors) == 0

    # Inspect order history from static record
    order_count, explanation, is_complete = extract_order_history_info(data)

    # When live=True, query live API if static record is incomplete or if live verification is requested
    if live and is_valid_schema:
        live_count, live_expl, live_ok = fetch_live_order_count(data)
        if live_ok and live_count is not None:
            order_count = live_count
            explanation = live_expl
            is_complete = True
        else:
            explanation = f"{explanation} (Live query note: {live_expl})".strip()

    if not is_complete or order_count is None:
        # Strictly classified as UNKNOWN per requirement
        classification = AccountClassification.UNKNOWN
        reason = f"Classified as unknown: {explanation}."
        if not is_valid_schema:
            reason += f" Schema validation issues: {'; '.join(errors)}"
    elif order_count == 0:
        classification = AccountClassification.FRESH
        reason = f"Classified as fresh: {explanation}."
    else:
        classification = AccountClassification.USED
        reason = f"Classified as used: {explanation}."

    is_refreshed = bool(data.get("_is_refreshed", False)) if isinstance(data, dict) else False
    if isinstance(data, dict):
        data.pop("_is_refreshed", None)

    return AccountRecordResult(
        source_file=source_reference,
        classification=classification,
        order_count=order_count,
        reason=reason,
        is_valid_schema=is_valid_schema,
        validation_errors=errors,
        account_id=user_id or None,
        mobile=mobile or None,
        is_refreshed=is_refreshed,
        raw_record=data
    )


def classify_account_file(file_path: Union[str, Path], live: bool = False) -> AccountRecordResult:
    """Parses a single JSON file and classifies it."""
    p = Path(file_path).resolve()
    if not p.is_file():
        return AccountRecordResult(
            source_file=str(file_path),
            classification=AccountClassification.UNKNOWN,
            is_valid_schema=False,
            validation_errors=[f"File not found: {file_path}"],
            reason="Source file does not exist on disk."
        )

    try:
        with open(p, "r", encoding="utf-8") as f:
            content = json.load(f)
    except Exception as e:
        return AccountRecordResult(
            source_file=str(file_path),
            classification=AccountClassification.UNKNOWN,
            is_valid_schema=False,
            validation_errors=[f"JSON parse error: {e}"],
            reason=f"Failed to parse JSON content: {e}"
        )

    return classify_account_data(content, source_reference=str(p), live=live)


def classify_multiple_accounts(
    file_paths: List[Union[str, Path]],
    output_dir: Optional[Union[str, Path]] = None,
    live: bool = False
) -> ClassificationSummary:
    """
    Processes multiple account JSON files without stopping on invalid files.
    Generates structured summary and optionally writes separate output files.
    """
    fresh_list: List[AccountRecordResult] = []
    used_list: List[AccountRecordResult] = []
    unknown_list: List[AccountRecordResult] = []
    errors_list: List[Dict[str, Any]] = []

    for fp in file_paths:
        res = classify_account_file(fp, live=live)
        if not res.is_valid_schema:
            errors_list.append({
                "file": res.source_file,
                "errors": res.validation_errors,
                "reason": res.reason
            })

        if res.classification == AccountClassification.FRESH:
            fresh_list.append(res)
        elif res.classification == AccountClassification.USED:
            used_list.append(res)
        else:
            unknown_list.append(res)

    summary = ClassificationSummary(
        total_scanned=len(file_paths),
        fresh_count=len(fresh_list),
        used_count=len(used_list),
        unknown_count=len(unknown_list),
        invalid_files_count=len(errors_list),
        fresh_accounts=fresh_list,
        used_accounts=used_list,
        unknown_accounts=unknown_list,
        errors=errors_list
    )

    # Write separate output files if output_dir specified
    if output_dir:
        out_p = Path(output_dir).resolve()
        out_p.mkdir(parents=True, exist_ok=True)

        def dump_json(target_filename: str, records: List[AccountRecordResult]):
            file_dest = out_p / target_filename
            with open(file_dest, "w", encoding="utf-8") as f:
                json.dump([r.to_dict(include_raw=True) for r in records], f, indent=2, ensure_ascii=False)

        dump_json("fresh_accounts.json", fresh_list)
        dump_json("used_accounts.json", used_list)
        dump_json("unknown_accounts.json", unknown_list)
        dump_json(f"fresh_{len(fresh_list)}.json", fresh_list)
        dump_json(f"used_{len(used_list)}.json", used_list)
        dump_json(f"combined_{len(fresh_list) + len(used_list)}.json", fresh_list + used_list)

        summary_file = out_p / "classification_summary.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary.to_dict(), f, indent=2, ensure_ascii=False)

        logger.info(f"Classification outputs saved to {out_p}")

    return summary


def classify_accounts_from_source(
    source: Any,
    output_dir: Optional[Union[str, Path]] = None,
    format_type: str = "combined_json",
    live: bool = False
) -> ClassificationSummary:
    """
    Classifies accounts from any supported input format (single JSON, folder,
    merged JSON array, or JSONL) and exports results as 1-by-1 .txt files
    in category subfolders ('fresh/', 'used/', 'unknown/') or combined JSON files.
    """
    from data_io import load_accounts_from_source, export_categorized_results

    records = load_accounts_from_source(source)
    fresh_list: List[AccountRecordResult] = []
    used_list: List[AccountRecordResult] = []
    unknown_list: List[AccountRecordResult] = []
    errors_list: List[Dict[str, Any]] = []

    def _worker(rec_item):
        src_ref = rec_item.get("source_reference", "<inline>")
        return classify_account_data(rec_item, source_reference=src_ref, live=live)

    if live and len(records) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(6, len(records))) as executor:
            classified_results = list(executor.map(_worker, records))
    else:
        classified_results = [_worker(r) for r in records]

    for res in classified_results:
        if not res.is_valid_schema:
            errors_list.append({
                "source": res.source_file,
                "errors": res.validation_errors,
                "reason": res.reason
            })

        if res.classification == AccountClassification.FRESH:
            fresh_list.append(res)
        elif res.classification == AccountClassification.USED:
            used_list.append(res)
        else:
            unknown_list.append(res)

    summary = ClassificationSummary(
        total_scanned=len(records),
        fresh_count=len(fresh_list),
        used_count=len(used_list),
        unknown_count=len(unknown_list),
        invalid_files_count=len(errors_list),
        fresh_accounts=fresh_list,
        used_accounts=used_list,
        unknown_accounts=unknown_list,
        errors=errors_list
    )

    if output_dir:
        categorized = {
            "fresh": [r.to_dict(include_raw=True) for r in fresh_list],
            "used": [r.to_dict(include_raw=True) for r in used_list],
            "unknown": [r.to_dict(include_raw=True) for r in unknown_list]
        }
        export_categorized_results(categorized, output_dir=output_dir, format_type=format_type)

        out_p = Path(output_dir).resolve()
        try:
            with open(out_p / f"fresh_{len(fresh_list)}.json", "w", encoding="utf-8") as f:
                json.dump(summary.get_fresh_account_records(), f, indent=2, ensure_ascii=False)
            with open(out_p / f"used_{len(used_list)}.json", "w", encoding="utf-8") as f:
                json.dump(summary.get_used_account_records(), f, indent=2, ensure_ascii=False)
            with open(out_p / f"combined_{len(fresh_list) + len(used_list)}.json", "w", encoding="utf-8") as f:
                json.dump(summary.get_combined_account_records(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Could not write clean count-based JSONs to {out_p}: {e}")

    return summary


