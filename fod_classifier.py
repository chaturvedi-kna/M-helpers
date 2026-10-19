"""
First Order Discount (FOD) Promotional Information and Tier Classifier.
Extracts, normalizes, and classifies advertised first-order promotional offers
while strictly distinguishing advertised discount amounts from verified eligibility.
"""

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union, Set

logger = logging.getLogger("FODClassifier")


class EligibilityStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNVERIFIED = "UNVERIFIED"


@dataclass
class FODRecord:
    """Parsed FOD offer details for a single record."""
    source_reference: str
    account_id: Optional[str]
    mobile: Optional[str]
    advertised_tier: str                       # e.g. 'tier_170', 'tier_190', 'tier_75', 'no_offer', 'unclassified'
    advertised_amount: Optional[int]           # e.g. 170, 190, 75
    raw_offer_text: Optional[str]
    eligibility_status: EligibilityStatus
    eligibility_reason: str
    is_duplicate: bool = False
    validation_errors: List[str] = field(default_factory=list)
    raw_data: Optional[Dict[str, Any]] = None

    def to_dict(self, include_raw: bool = False) -> Dict[str, Any]:
        d = {
            "source_reference": self.source_reference,
            "account_id": self.account_id,
            "mobile": self.mobile,
            "advertised_tier": self.advertised_tier,
            "advertised_amount": self.advertised_amount,
            "raw_offer_text": self.raw_offer_text,
            "eligibility_status": self.eligibility_status.value,
            "eligibility_reason": self.eligibility_reason,
            "is_duplicate": self.is_duplicate,
            "validation_errors": self.validation_errors
        }
        if include_raw and self.raw_data is not None:
            d["raw_data"] = self.raw_data
        return d


@dataclass
class FODClassificationSummary:
    """Summary of bulk FOD classification run."""
    total_scanned: int
    unique_accounts_count: int
    duplicates_count: int
    invalid_records_count: int
    tier_counts: Dict[str, int] = field(default_factory=dict)
    eligibility_counts: Dict[str, int] = field(default_factory=dict)
    records_by_tier: Dict[str, List[FODRecord]] = field(default_factory=dict)
    invalid_records: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": {
                "total_scanned": self.total_scanned,
                "unique_accounts_count": self.unique_accounts_count,
                "duplicates_count": self.duplicates_count,
                "invalid_records_count": self.invalid_records_count,
                "tier_counts": self.tier_counts,
                "eligibility_counts": self.eligibility_counts
            },
            "invalid_records": self.invalid_records
        }


def normalize_discount_amount(text: Optional[str]) -> Optional[int]:
    """
    Extracts advertised numeric discount amount from promotional text.
    Handles ₹170, Rs. 170, 170 off, Flat 190, etc.
    """
    if not text or not isinstance(text, str):
        return None

    cleaned = text.strip()

    # Pattern 1: ₹170 or Rs. 170 or INR 170
    match = re.search(r"(?:₹|rs\.?|inr)\s*(\d+)", cleaned, re.IGNORECASE)
    if match:
        return int(match.group(1))

    # Pattern 2: 170 off / 190 discount
    match = re.search(r"(\d+)\s*(?:off|discount|\/-)", cleaned, re.IGNORECASE)
    if match:
        return int(match.group(1))

    # Pattern 3: Direct pure digits
    if cleaned.isdigit():
        return int(cleaned)

    return None


def extract_fod_from_record(data: Dict[str, Any]) -> Tuple[Optional[str], Optional[int], EligibilityStatus, str]:
    """
    Extracts raw offer text, advertised amount, eligibility status, and rationale from record.
    Returns (raw_offer_text, advertised_amount, eligibility_status, rationale).
    """
    raw_text = None
    amount = None
    status = EligibilityStatus.UNVERIFIED
    rationale = "Offer details found in record, eligibility unverified against active cart."

    # Source 1: surgical_first_order_discount_v3 (from Meesho personalisation endpoint)
    if "surgical_first_order_discount_v3" in data and isinstance(data["surgical_first_order_discount_v3"], dict):
        s_fod = data["surgical_first_order_discount_v3"]
        offer = s_fod.get("offer") or {}
        raw_text = offer.get("offer_text") or offer.get("offer_subtitle") or ""
        amount = offer.get("max_offer_value") or s_fod.get("offer_bucket") or normalize_discount_amount(raw_text)
        is_enabled = s_fod.get("enabled", True)
        if not is_enabled:
            status = EligibilityStatus.INELIGIBLE
            rationale = "FOD feature disabled in surgical_first_order_discount_v3"
        else:
            status = EligibilityStatus.UNVERIFIED
            rationale = "Advertised in surgical_first_order_discount_v3; eligibility pending first order verification"
        return raw_text, amount, status, rationale

    # Source 2: explicit 'fod' or 'fod_offer' or 'first_order_discount'
    for k in ("fod", "fod_offer", "first_order_discount", "advertised_offer"):
        if k in data:
            val = data[k]
            if isinstance(val, dict):
                raw_text = val.get("description") or val.get("text") or val.get("offer_text") or val.get("title")
                amount = val.get("amount") or val.get("discount") or normalize_discount_amount(raw_text)
                if val.get("status") == "ELIGIBLE" or val.get("eligible") is True:
                    status = EligibilityStatus.ELIGIBLE
                    rationale = f"Explicitly marked ELIGIBLE in '{k}'"
                elif val.get("status") == "INELIGIBLE" or val.get("eligible") is False:
                    status = EligibilityStatus.INELIGIBLE
                    rationale = f"Explicitly marked INELIGIBLE in '{k}'"
                return raw_text, amount, status, rationale

            elif isinstance(val, (str, int)):
                raw_text = str(val)
                amount = normalize_discount_amount(raw_text) if isinstance(val, str) else int(val)
                return raw_text, amount, status, rationale

    # Source 3: offers list (e.g. from /api/v3/offers/list)
    offers_list = data.get("offers_details") or data.get("offers") or []
    if isinstance(offers_list, list):
        for o in offers_list:
            if isinstance(o, dict):
                desc_obj = o.get("offer_description") or {}
                t = desc_obj.get("title") or desc_obj.get("description") or o.get("title") or ""
                if "first" in t.lower() or "1st" in t.lower() or "fod" in t.lower():
                    raw_text = t
                    amount = normalize_discount_amount(raw_text)
                    if o.get("status") == "ELIGIBLE":
                        status = EligibilityStatus.ELIGIBLE
                        rationale = f"Offer list explicitly verified ELIGIBLE: {t}"
                    elif o.get("status") == "INELIGIBLE":
                        status = EligibilityStatus.INELIGIBLE
                        rationale = f"Offer list marked INELIGIBLE ({desc_obj.get('ineligible_description') or 'conditions not met'})"
                    return raw_text, amount, status, rationale

    # Source 4: root-level keys
    for k in ("fod_tier", "fod_text", "offer_text"):
        if k in data and isinstance(data[k], str):
            raw_text = data[k]
            amount = normalize_discount_amount(raw_text)
            return raw_text, amount, status, rationale

    # Source 5: user_meta first order indicator
    user_meta = data.get("user_meta") or {}
    if isinstance(user_meta, dict) and user_meta.get("is_first_order") is False:
        return None, None, EligibilityStatus.INELIGIBLE, "user_meta indicates not a first order account"

    return None, None, EligibilityStatus.UNVERIFIED, "No FOD promotional data found in record"


def classify_single_fod_record(
    data: Any,
    source_reference: str = "<inline>"
) -> FODRecord:
    """Parses and categorizes a single record into an advertised FOD tier."""
    errors: List[str] = []
    if not isinstance(data, dict):
        return FODRecord(
            source_reference=source_reference,
            account_id=None,
            mobile=None,
            advertised_tier="unclassified",
            advertised_amount=None,
            raw_offer_text=None,
            eligibility_status=EligibilityStatus.UNVERIFIED,
            eligibility_reason="Invalid JSON format",
            validation_errors=["Record must be a JSON object (dict)."]
        )

    account_id = str(data.get("user_id") or "")
    mobile = str(data.get("mobile") or data.get("phone") or "")

    raw_text, amount, eligibility_status, rationale = extract_fod_from_record(data)

    # Determine tier
    if amount is not None:
        tier_name = f"tier_{amount}"
    elif raw_text:
        tier_name = "unclassified"
        errors.append(f"Offer text '{raw_text}' found but could not extract numeric discount amount.")
    else:
        tier_name = "no_offer"
        rationale = "No FOD promotional offer present in record."

    return FODRecord(
        source_reference=source_reference,
        account_id=account_id or None,
        mobile=mobile or None,
        advertised_tier=tier_name,
        advertised_amount=amount,
        raw_offer_text=raw_text,
        eligibility_status=eligibility_status,
        eligibility_reason=rationale,
        validation_errors=errors,
        raw_data=data
    )


def classify_multiple_fod_files(
    file_paths: List[Union[str, Path]],
    output_dir: Optional[Union[str, Path]] = None
) -> FODClassificationSummary:
    """
    Parses multiple files for FOD promotion data, handles duplicates safely,
    groups records by tier, and writes consolidated JSON files.
    """
    records_by_tier: Dict[str, List[FODRecord]] = {}
    tier_counts: Dict[str, int] = {}
    eligibility_counts: Dict[str, int] = {
        EligibilityStatus.ELIGIBLE.value: 0,
        EligibilityStatus.INELIGIBLE.value: 0,
        EligibilityStatus.UNVERIFIED.value: 0
    }
    seen_identifiers: Set[str] = set()
    duplicates_count = 0
    invalid_records: List[Dict[str, Any]] = []

    for fp in file_paths:
        p = Path(fp).resolve()
        if not p.is_file():
            invalid_records.append({"source": str(fp), "error": "File not found"})
            continue

        try:
            with open(p, "r", encoding="utf-8") as f:
                content = json.load(f)
        except Exception as e:
            invalid_records.append({"source": str(fp), "error": f"JSON parse error: {e}"})
            continue

        record = classify_single_fod_record(content, source_reference=str(p))

        # Check duplicate
        identifier = record.account_id or record.mobile or str(p)
        if identifier in seen_identifiers:
            record.is_duplicate = True
            duplicates_count += 1
        else:
            seen_identifiers.add(identifier)

        if record.validation_errors:
            invalid_records.append({
                "source": record.source_reference,
                "errors": record.validation_errors
            })

        tier = record.advertised_tier
        if tier not in records_by_tier:
            records_by_tier[tier] = []
            tier_counts[tier] = 0

        records_by_tier[tier].append(record)
        tier_counts[tier] += 1
        eligibility_counts[record.eligibility_status.value] += 1

    summary = FODClassificationSummary(
        total_scanned=len(file_paths),
        unique_accounts_count=len(seen_identifiers),
        duplicates_count=duplicates_count,
        invalid_records_count=len(invalid_records),
        tier_counts=tier_counts,
        eligibility_counts=eligibility_counts,
        records_by_tier=records_by_tier,
        invalid_records=invalid_records
    )

    # Write consolidated outputs
    if output_dir:
        out_p = Path(output_dir).resolve()
        out_p.mkdir(parents=True, exist_ok=True)

        for tier, records in records_by_tier.items():
            tier_file = out_p / f"fod_{tier}.json"
            with open(tier_file, "w", encoding="utf-8") as f:
                json.dump([r.to_dict(include_raw=True) for r in records], f, indent=2, ensure_ascii=False)

        summary_file = out_p / "fod_summary.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary.to_dict(), f, indent=2, ensure_ascii=False)

        logger.info(f"Consolidated FOD files saved to {out_p}")

    return summary


def fetch_live_fod(
    client=None,
    account_data: Optional[Dict[str, Any]] = None,
    custom_proxy: Optional[str] = None
) -> FODRecord:
    """
    Fetches the active, real-time First Order Discount (FOD) directly from Meesho APIs.
    Calls /api/1.0/anonymous/fod-personalisation (and /api/3.0/offers/list if authenticated),
    ensuring live daily promotional tiers are captured rather than relying on static JSON files.
    """
    import uuid
    import requests
    from config import DEFAULT_API_HOST, DEFAULT_AUTHORIZATION, APP_VERSION, APP_VERSION_CODE, APP_SDK_VERSION
    import proxy_manager

    base_url = (client.api_host if client else DEFAULT_API_HOST).rstrip("/")

    # Build device identity
    acc = account_data or (client.account_data if client else {})
    identity = acc.get("identity") or {}

    instance_id = str(acc.get("instance_id") or identity.get("instance_id") or uuid.uuid4().hex)
    gaid = str(acc.get("gaid") or identity.get("gaid") or uuid.uuid4())
    auth = str(acc.get("authorization") or identity.get("authorization") or DEFAULT_AUTHORIZATION)
    xo = str(acc.get("xo") or "")

    # Build headers - Meesho /api/1.0/anonymous/fod-personalisation strictly requires anonymous context
    anon_xo = identity.get("anon_xo") or xo or "eyJ0eXBlIjoiY29tcG9zaXRlIn0=.eyJqd3QiOiJleUpoYkdjaU9pSklVekkxTmlJc0ltaDBkSEJ6T2k4dmJXVmxjMmh2TG1OdmJTOXBjMjlmWTI5MWJuUnllVjlqYjJSbElqb2lTVTRpTENKb2RIUndjem92TDIxbFpYTm9ieTVqYjIwdmRtVnljMmx2YmlJNklqRWlMQ0owZVhBaU9pSktWMVFpZlEuZXlKbGVIQWlPakU1TkRrek5ERTJNek1zSW1oMGRIQnpPaTh2YldWbGMyaHZMbU52YlM5aGJtOXVlVzF2ZFhOZmRYTmxjbDlwWkNJNkltWXdPRFk1WXpZeExUUTJaRGt0TkROak1DMWlOek0xTFdZeFpESm1aREF4WlRReU15SXNJbWgwZEhCek9pOHZiV1ZsYzJodkxtTnZiUzlwYm5OMFlXNWpaVjlwWkNJNklqSXdZVFF5T1dFNVl6VmxOalF4T1dWaE1EQmlOekk0T0RrM01XSm1PVEkySWl3aWFXRjBJam94TnpreE5qWXhOak16ZlEueDB6ZDZDT0pkVU1tTk5XeFVib2RKcHMyNUJ5cDZFcXVKSTNaTVBpbkpoQSIsInhvIjoiIn0="
    user_id = str(acc.get("user_id") or "")

    headers = {
        "Authorization": auth,
        "App-Version": str(acc.get("app_version", "29.6")),
        "App-Version-Code": str(acc.get("app_version_code", "870")),
        "Instance-Id": instance_id,
        "App-Gaid": gaid,
        "App-Session-Count": "1",
        "Country-Iso": "in",
        "Application-Id": "com.meesho.supply",
        "App-Session-Id": str(identity.get("app_session_id") or "d3515637-9831-45a7-9a7e-ebec00c46d0d"),
        "App-Sdk-Version": "34",
        "App-Client-Id": "android",
        "Xo": anon_xo,
        "Meesho-User-Context": "anonymous",
        "Accept": "application/json",
        "Content-Type": "application/json; charset=UTF-8",
        "User-Agent": "okhttp/4.9.0"
    }

    body = {
        "offer_bucket": "75",
        "from_language_modal": True
    }

    sess = client.session if client else requests.Session()
    endpoint = f"{base_url}/api/1.0/anonymous/fod-personalisation"
    acc_key = user_id or str(acc.get("mobile") or "")

    try:
        r = proxy_manager.request_with_retry(
            session=sess,
            method="POST",
            url=endpoint,
            account_key=acc_key or None,
            headers=headers,
            json=body,
            timeout=15,
            custom_proxy=custom_proxy or (client.custom_proxy if client else None)
        )
        r.raise_for_status()
        live_payload = r.json()
    except Exception as e:
        logger.error(f"Live FOD fetch from {endpoint} failed: {e}")
        return FODRecord(
            source_reference=endpoint,
            account_id=str(acc.get("user_id") or ""),
            mobile=str(acc.get("mobile") or ""),
            advertised_tier="unclassified",
            advertised_amount=None,
            raw_offer_text=None,
            eligibility_status=EligibilityStatus.UNVERIFIED,
            eligibility_reason=f"Failed to query live Meesho API: {e}",
            validation_errors=[str(e)]
        )

    # Classify the live response
    record = classify_single_fod_record(
        live_payload,
        source_reference=f"{endpoint} (LIVE)"
    )
    record.account_id = str(acc.get("user_id") or "") or None
    record.mobile = str(acc.get("mobile") or "") or None
    return record


def classify_fod_from_source(
    source: Any,
    live: bool = False,
    output_dir: Optional[Union[str, Path]] = None,
    format_type: str = "combined_json",
    client=None,
    custom_proxy: Optional[str] = None
) -> FODClassificationSummary:
    """
    Classifies FOD offers from any input format (single JSON, folder, merged array, JSONL).
    If live=True, queries Meesho's live API per account using sticky account proxies.
    Exports results as 1-by-1 .txt files in categorized tier subfolders or combined JSON.
    """
    from data_io import load_accounts_from_source, export_categorized_results

    accounts = load_accounts_from_source(source)
    records_by_tier: Dict[str, List[FODRecord]] = {}
    tier_counts: Dict[str, int] = {}
    eligibility_counts: Dict[str, int] = {
        EligibilityStatus.ELIGIBLE.value: 0,
        EligibilityStatus.INELIGIBLE.value: 0,
        EligibilityStatus.UNVERIFIED.value: 0
    }
    seen_identifiers: Set[str] = set()
    duplicates_count = 0
    invalid_records: List[Dict[str, Any]] = []

    for acc in accounts:
        src_ref = acc.get("source_reference", "<inline>")
        if live:
            record = fetch_live_fod(client=client, account_data=acc, custom_proxy=custom_proxy)
            record.source_reference = src_ref
        else:
            record = classify_single_fod_record(acc, source_reference=src_ref)

        identifier = record.account_id or record.mobile or src_ref
        if identifier in seen_identifiers:
            record.is_duplicate = True
            duplicates_count += 1
        else:
            seen_identifiers.add(identifier)

        if record.validation_errors:
            invalid_records.append({
                "source": record.source_reference,
                "errors": record.validation_errors
            })

        tier = record.advertised_tier
        if tier not in records_by_tier:
            records_by_tier[tier] = []
            tier_counts[tier] = 0

        records_by_tier[tier].append(record)
        tier_counts[tier] += 1
        eligibility_counts[record.eligibility_status.value] += 1

    summary = FODClassificationSummary(
        total_scanned=len(accounts),
        unique_accounts_count=len(seen_identifiers),
        duplicates_count=duplicates_count,
        invalid_records_count=len(invalid_records),
        tier_counts=tier_counts,
        eligibility_counts=eligibility_counts,
        records_by_tier=records_by_tier,
        invalid_records=invalid_records
    )

    if output_dir:
        categorized = {}
        for tier, recs in records_by_tier.items():
            categorized[tier] = [r.to_dict(include_raw=True) for r in recs]
        export_categorized_results(categorized, output_dir=output_dir, format_type=format_type)

    return summary



